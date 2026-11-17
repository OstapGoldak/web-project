import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

import httpx
from fastapi import FastAPI, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

from shared.database import get_db
from shared.models import Task
from shared.gauss import validate, TaskLimitError

MAX_TASKS_PER_USER = 10
CURRENT_USER_ID = 1  # тимчасово, до авторизації
WORKER_URLS = os.getenv("WORKER_URLS", "http://localhost:8001").split(",")

app = FastAPI()


class TaskCreate(BaseModel):
    a: list[list[float]]
    b: list[float]


def pick_worker() -> str:
    return WORKER_URLS[0]  # балансування додамо на кроці 7


@app.post("/tasks")
def create_task(data: TaskCreate, db: Session = Depends(get_db)):
    try:
        validate(data.a, data.b)
    except TaskLimitError as e:
        raise HTTPException(400, str(e))

    count = db.query(Task).filter(Task.user_id == CURRENT_USER_ID).count()
    if count >= MAX_TASKS_PER_USER:
        raise HTTPException(400, f"Максимум {MAX_TASKS_PER_USER} задач, видаліть старі")

    task = Task(
        user_id=CURRENT_USER_ID,
        size=len(data.a),
        input_data={"a": data.a, "b": data.b},
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    url = pick_worker()
    task.worker_url = url
    db.commit()
    try:
        httpx.post(f"{url}/run/{task.id}", timeout=5).raise_for_status()
    except httpx.HTTPError:
        task.status = "failed"
        task.error = "Воркер недоступний"
        task.finished_at = datetime.utcnow()
        db.commit()
        raise HTTPException(503, "Воркер недоступний")

    return {"id": task.id, "status": task.status}


@app.get("/tasks")
def list_tasks(db: Session = Depends(get_db)):
    tasks = (
        db.query(Task)
        .filter(Task.user_id == CURRENT_USER_ID)
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
def get_task(task_id: int, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task or task.user_id != CURRENT_USER_ID:
        raise HTTPException(404, "Задачу не знайдено")
    return {
        "id": task.id,
        "status": task.status,
        "progress": task.progress,
        "size": task.size,
        "result": task.result,
        "error": task.error,
        "created_at": task.created_at,
        "finished_at": task.finished_at,
    }


@app.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: int, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task or task.user_id != CURRENT_USER_ID:
        raise HTTPException(404, "Задачу не знайдено")
    if task.status not in ("queued", "running"):
        raise HTTPException(400, "Задача вже завершена")
    task.status = "cancelled"
    db.commit()
    return {"id": task.id, "status": task.status}


@app.delete("/tasks/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task or task.user_id != CURRENT_USER_ID:
        raise HTTPException(404, "Задачу не знайдено")
    db.delete(task)
    db.commit()
    return {"deleted": task_id}


FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")