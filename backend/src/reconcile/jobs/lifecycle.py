from __future__ import annotations

import logging
import socket
import threading
from collections.abc import Callable
from datetime import datetime

from sqlalchemy.orm import Session

from reconcile.domain.types import JobStatus
from reconcile.jobs.queue import run_once

log = logging.getLogger(__name__)


class LifecycleConsumer:
    """A concurrency-one worker that sleeps without touching PostgreSQL when idle."""

    def __init__(self, session_factory: Callable[[], Session]):
        self._session_factory = session_factory
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self._state = "starting"
        self._owner = f"web:{socket.gethostname()}"

    @property
    def state(self) -> str:
        with self._state_lock:
            return self._state

    def _set_state(self, value: str) -> None:
        with self._state_lock:
            self._state = value

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="reconcile-job-consumer",
            daemon=True,
        )
        self._thread.start()
        self.wake()

    def wake(self) -> None:
        self._wake.set()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self._set_state("stopped")

    def _run(self) -> None:
        while not self._stop.is_set():
            self._set_state("idle")
            self._wake.wait()
            self._wake.clear()
            if self._stop.is_set():
                break
            while not self._stop.is_set():
                self._set_state("running")
                try:
                    with self._session_factory() as session:
                        job = run_once(session, owner=self._owner)
                except Exception:
                    log.exception("job consumer unavailable until the next wake signal")
                    self._set_state("unavailable")
                    break
                if job is None:
                    break
                if job.status == JobStatus.PENDING.value:
                    remaining = job.available_at - datetime.now(job.available_at.tzinfo)
                    delay = max(remaining.total_seconds(), 0)
                    if self._stop.wait(delay):
                        break
        self._set_state("stopped")
