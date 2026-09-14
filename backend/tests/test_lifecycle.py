from __future__ import annotations

import time
from contextlib import AbstractContextManager
from threading import Event
from types import SimpleNamespace

from sqlalchemy.orm import Session

from reconcile.jobs import lifecycle


class _SessionContext(AbstractContextManager[Session]):
    def __enter__(self) -> Session:
        return SimpleNamespace()  # type: ignore[return-value]

    def __exit__(self, *args: object) -> None:
        return None


def test_lifecycle_consumer_drains_then_waits_for_a_real_wake(monkeypatch) -> None:
    calls = 0
    drained = Event()

    def fake_run_once(session: Session, *, owner: str) -> object | None:
        nonlocal calls
        del session, owner
        calls += 1
        if calls in {2, 3}:
            drained.set()
            return None
        return SimpleNamespace(status="SUCCEEDED")

    monkeypatch.setattr(lifecycle, "run_once", fake_run_once)
    consumer = lifecycle.LifecycleConsumer(_SessionContext)
    consumer.start()
    assert drained.wait(1)
    first_drain_calls = calls
    time.sleep(0.05)
    assert calls == first_drain_calls

    drained.clear()
    consumer.wake()
    assert drained.wait(1)
    consumer.stop()
    assert calls == first_drain_calls + 1
    assert consumer.state == "stopped"
