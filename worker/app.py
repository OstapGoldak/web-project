import sys
import threading
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI

from shared.database import SessionLocal
from shared.models import Task
from shared.gauss import solve, TaskLimitError, TaskCancelled, SingularMatrixError

app = FastAPI()

active = 0
lock = threading.Lock()


def run_task(task_id: int):
    global active
    with lock:
        active += 1
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if task is None or task.status == "cancelled":
            return
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
        with lock:
            active -= 1


@app.post("/run/{task_id}")
def run(task_id: int):
    threading.Thread(target=run_task, args=(task_id,), daemon=True).start()
    return {"accepted": task_id}


@app.get("/load")
def load():
    return {"active": active}