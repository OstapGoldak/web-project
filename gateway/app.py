import os
import random
import sys
from datetime import datetime
from pathlib import Path
from pydantic import Field

sys.path.append(str(Path(__file__).resolve().parent.parent))

import httpx
from fastapi import FastAPI, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

from shared.database import get_db
from shared.models import Task
from shared.gauss import validate, TaskLimitError
from shared.models import User
from shared.auth import hash_password, verify_password, create_token
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from shared.auth import decode_token
from contextlib import asynccontextmanager
from gateway.cluster import cluster, MAX_WORKERS
from sqlalchemy import func

MAX_TASKS_PER_USER = 10
ADMIN_USERNAMES = {
    u.strip() for u in os.getenv("ADMIN_USERNAMES", "admin").split(",") if u.strip()
}

@asynccontextmanager
async def lifespan(app: FastAPI):
    cluster.start()
    yield
    cluster.stop()


app = FastAPI(lifespan=lifespan)


class TaskCreate(BaseModel):
    a: list[list[float]]
    b: list[float]

class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=4, max_length=72)

def pick_workers(db: Session) -> list[str]:
    urls = cluster.urls()
    loads = {
        url: db.query(Task)
        .filter(Task.worker_url == url, Task.status.in_(("queued", "running")))
        .count()
        for url in urls
    }
    random.shuffle(urls)
    return sorted(urls, key=lambda u: loads[u])


bearer = HTTPBearer(auto_error=False)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None:
        raise HTTPException(401, "Потрібна авторизація")
    user_id = decode_token(creds.credentials)
    user = db.get(User, user_id) if user_id is not None else None
    if user is None:
        raise HTTPException(401, "Недійсний токен")
    return user


def get_admin(user: User = Depends(get_current_user)) -> User:
    if user.username not in ADMIN_USERNAMES:
        raise HTTPException(403, "Доступ лише для адміністратора")
    return user


@app.post("/register")
def register(data: Credentials, db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == data.username).first():
        raise HTTPException(400, "Такий логін уже існує")
    user = User(username=data.username, password_hash=hash_password(data.password))
    db.add(user)
    db.commit()
    return {"id": user.id, "username": user.username}


@app.post("/login")
def login(data: Credentials, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == data.username).first()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(401, "Неправильний логін або пароль")
    return {"access_token": create_token(user.id)}


@app.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"id": user.id, "username": user.username, "is_admin": user.username in ADMIN_USERNAMES}


@app.get("/admin/stats")
def admin_stats(admin: User = Depends(get_admin), db: Session = Depends(get_db)):
    by_status = dict(db.query(Task.status, func.count(Task.id)).group_by(Task.status).all())
    active = (
        db.query(Task, User.username)
        .join(User, User.id == Task.user_id)
        .filter(Task.status.in_(("queued", "running")))
        .order_by(Task.id)
        .all()
    )
    return {
        "workers": cluster.snapshot(),
        "max_workers": MAX_WORKERS,
        "users": db.query(User).count(),
        "tasks_total": sum(by_status.values()),
        "tasks_by_status": by_status,
        "active_tasks": [
            {
                "id": t.id,
                "user": username,
                "status": t.status,
                "progress": t.progress,
                "size": t.size,
                "worker": t.worker_url,
            }
            for t, username in active
        ],
    }


@app.post("/tasks")
def create_task(data: TaskCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        validate(data.a, data.b)
    except TaskLimitError as e:
        raise HTTPException(400, str(e))

    count = db.query(Task).filter(Task.user_id == user.id).count()
    if count >= MAX_TASKS_PER_USER:
        raise HTTPException(400, f"Максимум {MAX_TASKS_PER_USER} задач, видаліть старі")

    task = Task(
        user_id=user.id,
        size=len(data.a),
        input_data={"a": data.a, "b": data.b},
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    for url in pick_workers(db):
        task.worker_url = url
        db.commit()
        try:
            reply = httpx.post(f"{url}/run/{task.id}", timeout=5)
            reply.raise_for_status()
            info = reply.json()
            return {
                "id": task.id,
                "status": task.status,
                "worker": url,
                "queued": info["queued"],
                "eta_seconds": info["eta_seconds"],
            }
        except httpx.HTTPError:
            continue 

    task.status = "failed"
    task.error = "Усі воркери недоступні"
    task.finished_at = datetime.utcnow()
    db.commit()
    raise HTTPException(503, "Усі воркери недоступні")


@app.get("/tasks")
def list_tasks(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    tasks = (
        db.query(Task)
        .filter(Task.user_id == user.id)
        .order_by(Task.id.desc())
        .all()
    )
    return [
        {
            "id": t.id,
            "status": t.status,
            "progress": t.progress,
            "size": t.size,
            "created_at": t.created_at,
        }
        for t in tasks
    ]


@app.get("/tasks/{task_id}")
def get_task(task_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task or task.user_id != user.id:
        raise HTTPException(404, "Задачу не знайдено")
    return {
        "id": task.id,
        "status": task.status,
        "progress": task.progress,
        "size": task.size,
        "result": task.result,
        "error": task.error,
        "worker": task.worker_url,
        "created_at": task.created_at,
        "finished_at": task.finished_at,
    }


@app.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task or task.user_id != user.id:
        raise HTTPException(404, "Задачу не знайдено")
    if task.status not in ("queued", "running"):
        raise HTTPException(400, "Задача вже завершена")
    task.status = "cancelled"
    db.commit()
    return {"id": task.id, "status": task.status}


@app.delete("/tasks/{task_id}")
def delete_task(task_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task or task.user_id != user.id:
        raise HTTPException(404, "Задачу не знайдено")
    db.delete(task)
    db.commit()
    return {"deleted": task_id}


FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")