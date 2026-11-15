import sys
import threading
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from shared.database import get_db, SessionLocal
from shared.models import Task
from gauss import solve, validate, TaskLimitError, TaskCancelled, SingularMatrixError

from fastapi.staticfiles import StaticFiles

MAX_TASKS_PER_USER = 10
CURRENT_USER_ID = 1  # тимчасово, до кроку з авторизацією

app = FastAPI()


class TaskCreate(BaseModel):
    a: list[list[float]]
    b: list[float]


def run_task(task_id: int):
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        task.status = "running"
        db.commit()

        def on_progress(p):
            task.progress = p
            db.commit()

        def is_cancelled():
            db.refresh(task)
            return task.status == "cancelled"

        try:
            x = solve(task.input_data["a"], task.input_data["b"], on_progress, is_cancelled)
            task.result = {"x": x}
            task.status = "done"
            task.progress = 100
        except TaskCancelled:
            task.status = "cancelled"
        except (TaskLimitError, SingularMatrixError) as e:
            task.status = "failed"
            task.error = str(e)
        task.finished_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()


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

    threading.Thread(target=run_task, args=(task.id,), daemon=True).start()
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