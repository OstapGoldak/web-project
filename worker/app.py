import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI

from shared.database import SessionLocal
from shared.models import Task
from shared.gauss import solve, TaskLimitError, TaskCancelled, SingularMatrixError

app = FastAPI()

MAX_ACTIVE = int(os.getenv("MAX_ACTIVE", "2"))
slots = threading.Semaphore(MAX_ACTIVE)
lock = threading.Lock()
running = 0
waiting = 0
durations = []


def average_duration():
    return sum(durations) / len(durations) if durations else 5.0


def run_task(task_id: int):
    global running, waiting
    slots.acquire()
    with lock:
        waiting -= 1
        running += 1
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if task is None or task.status == "cancelled":
            return
        task.status = "running"
        db.commit()
        started = time.monotonic()

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
            with lock:
                durations.append(time.monotonic() - started)
                del durations[:-10]
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
            running -= 1
        slots.release()


@app.post("/run/{task_id}")
def run(task_id: int):
    global waiting
    with lock:
        has_free_slot = running + waiting < MAX_ACTIVE
        ahead = waiting
        waiting += 1
        avg = average_duration()
    eta = 0 if has_free_slot else round((ahead + 1) / MAX_ACTIVE * avg)
    threading.Thread(target=run_task, args=(task_id,), daemon=True).start()
    return {"accepted": task_id, "queued": not has_free_slot, "eta_seconds": eta}


@app.get("/load")
def load():
    with lock:
        return {"active": running, "queued": waiting, "max_active": MAX_ACTIVE}