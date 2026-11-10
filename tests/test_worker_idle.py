"""Worker wake-up behaviour (no DB): idle workers must not query, notify() must start work promptly."""
import contextlib
import threading
import time

from backend.app.config import Settings
from backend.app.services import worker as W


class Harness:
    def __init__(self, monkeypatch, queued=0, run_seconds=0.0):
        self.claims = 0
        self.sweeps = 0
        self.done = []
        self.queue = list(range(queued))
        self.lock = threading.Lock()
        self.run_seconds = run_seconds
        monkeypatch.setattr(W, "fail_interrupted", lambda db: 0)
        monkeypatch.setattr(W, "claim_next", self.claim)
        monkeypatch.setattr(W, "sweep_timeouts", self.sweep)
        monkeypatch.setattr(W, "process_run", self.process)
        monkeypatch.setattr(W.Worker, "SWEEP_SECONDS", 0.05)
        settings = Settings(_env_file=None, worker_threads=2)
        self.worker = W.Worker(lambda: contextlib.nullcontext(None), settings, llm=None, embedder=None)

    def claim(self, db):
        with self.lock:
            self.claims += 1
            return self.queue.pop(0) if self.queue else None

    def sweep(self, db, timeout):
        with self.lock:
            self.sweeps += 1

    def process(self, Session, settings, llm, embedder, rid):
        time.sleep(self.run_seconds)
        self.done.append(rid)
        return "completed"

    def enqueue(self, rid):
        with self.lock:
            self.queue.append(rid)
        self.worker.notify()


def wait_for(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_idle_worker_sends_no_queries(monkeypatch):
    h = Harness(monkeypatch)
    h.worker.start()
    try:
        time.sleep(0.3)
        first = h.claims
        time.sleep(0.5)
        assert first <= 2 and h.claims == first  # one claim per thread at start, then silence
        assert h.sweeps == 0
    finally:
        h.worker.stop()


def test_queued_at_startup_and_notify_are_processed(monkeypatch):
    h = Harness(monkeypatch, queued=2)
    h.worker.start()
    try:
        assert wait_for(lambda: sorted(h.done) == [0, 1])
        for rid in (10, 11, 12):
            h.enqueue(rid)
        assert wait_for(lambda: sorted(h.done) == [0, 1, 10, 11, 12])
    finally:
        h.worker.stop()


def test_sweeps_only_while_a_run_is_in_progress(monkeypatch):
    h = Harness(monkeypatch, run_seconds=0.3)
    h.worker.start()
    try:
        h.enqueue(1)
        assert wait_for(lambda: h.done == [1])
        during = h.sweeps
        assert during >= 1
        time.sleep(0.4)
        assert h.sweeps == during  # idle again: no more sweeps
    finally:
        h.worker.stop()
