import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent

BASE_WORKERS = [
    u.strip()
    for u in os.getenv("WORKER_URLS", "http://localhost:8001,http://localhost:8002").split(",")
    if u.strip()
]
AUTOSCALE = os.getenv("AUTOSCALE", "1") == "1"
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "4"))
SCALE_DOWN_IDLE = float(os.getenv("SCALE_DOWN_IDLE", "30"))
CHECK_INTERVAL = float(os.getenv("SCALE_CHECK_INTERVAL", "2"))
FIRST_DYNAMIC_PORT = int(os.getenv("FIRST_DYNAMIC_PORT", "8010"))


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


class Cluster:
    def __init__(self):
        self.lock = threading.RLock()
        self.static = list(BASE_WORKERS)
        self.dynamic = {}
        self.stop_event = threading.Event()
        self.thread = None

    def urls(self) -> list[str]:
        with self.lock:
            return self.static + list(self.dynamic)

    def fetch_load(self, url: str) -> dict | None:
        try:
            reply = httpx.get(f"{url}/load", timeout=1.5)
            reply.raise_for_status()
            return reply.json()
        except (httpx.HTTPError, ValueError):
            return None

    def snapshot(self) -> list[dict]:
        result = []
        for url in self.urls():
            load = self.fetch_load(url)
            result.append(
                {
                    "url": url,
                    "alive": load is not None,
                    "active": load["active"] if load else 0,
                    "queued": load["queued"] if load else 0,
                    "max_active": load["max_active"] if load else 0,
                    "dynamic": url in self.dynamic,
                }
            )
        return result

    def spawn_worker(self) -> str | None:
        port = FIRST_DYNAMIC_PORT
        used = {int(u.rsplit(":", 1)[1]) for u in self.urls()}
        while port in used or not port_is_free(port):
            port += 1
        url = f"http://localhost:{port}"
        proc = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", "worker.app:app",
                "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning",
            ],
            cwd=ROOT,
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return None
            if self.fetch_load(url) is not None:
                with self.lock:
                    self.dynamic[url] = {"proc": proc, "idle_since": None}
                print(f"[autoscale] запущено воркер {url}")
                return url
            time.sleep(0.5)
        proc.terminate()
        return None

    def remove_worker(self, url: str):
        with self.lock:
            entry = self.dynamic.pop(url, None)
        if entry:
            entry["proc"].terminate()
            try:
                entry["proc"].wait(timeout=5)
            except subprocess.TimeoutExpired:
                entry["proc"].kill()
            print(f"[autoscale] зупинено воркер {url}")

    def autoscale_step(self):
        with self.lock:
            for url, entry in list(self.dynamic.items()):
                if entry["proc"].poll() is not None:
                    del self.dynamic[url]

        stats = self.snapshot()
        alive = [w for w in stats if w["alive"]]
        saturated = not alive or all(w["active"] + w["queued"] >= w["max_active"] for w in alive)

        if saturated and len(stats) < MAX_WORKERS:
            self.spawn_worker()
            return

        now = time.monotonic()
        for w in stats:
            if not w["dynamic"]:
                continue
            entry = self.dynamic.get(w["url"])
            if entry is None:
                continue
            if w["active"] + w["queued"] == 0:
                if entry["idle_since"] is None:
                    entry["idle_since"] = now
                elif now - entry["idle_since"] > SCALE_DOWN_IDLE and len(stats) > len(self.static):
                    self.remove_worker(w["url"])
                    return
            else:
                entry["idle_since"] = None

    def loop(self):
        while not self.stop_event.wait(CHECK_INTERVAL):
            try:
                self.autoscale_step()
            except Exception as e:
                print(f"[autoscale] помилка: {e}")

    def start(self):
        if AUTOSCALE and self.thread is None:
            self.thread = threading.Thread(target=self.loop, daemon=True)
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        for url in list(self.dynamic):
            self.remove_worker(url)


cluster = Cluster()