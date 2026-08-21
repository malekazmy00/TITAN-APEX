"""Unit tests for src/queue/enqueue.py.

The queue is always injected: these tests never touch a real Redis/RQ.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.core.exceptions import QueueError
from src.queue.enqueue import enqueue_crawl
from src.queue.tasks import run_spider_job


class _FakeJob:
    def __init__(self, job_id: str) -> None:
        self.id = job_id


class _FakeQueue:
    def __init__(self, job_id: str = "job-123") -> None:
        self._job_id = job_id
        self.calls: list[tuple[Any, ...]] = []

    def enqueue(self, fn: Any, *args: Any) -> _FakeJob:
        self.calls.append((fn, *args))
        return _FakeJob(self._job_id)


class _BrokenQueue:
    def enqueue(self, fn: Any, *args: Any) -> _FakeJob:
        raise QueueError("queue backend unreachable")


def test_enqueue_crawl_returns_the_job_id() -> None:
    """Happy path: enqueuing returns the id RQ assigned to the job."""
    fake_queue = _FakeQueue(job_id="abc-123")

    job_id = enqueue_crawl("configs/target.yaml", queue=fake_queue)

    assert job_id == "abc-123"
    assert fake_queue.calls == [(run_spider_job, "configs/target.yaml")]


def test_enqueue_crawl_propagates_queue_error() -> None:
    """Failure case 1: a broken queue backend surfaces as QueueError, not swallowed."""
    with pytest.raises(QueueError, match="queue backend unreachable"):
        enqueue_crawl("configs/target.yaml", queue=_BrokenQueue())


def test_enqueue_crawl_uses_the_given_queue_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure-adjacent case 2: without an injected queue, a real Queue must be built
    with the requested queue_name (and never silently default to something else)."""
    captured: dict[str, Any] = {}

    class _FakeRealQueue:
        def __init__(self, name: str, connection: Any) -> None:
            captured["name"] = name
            captured["connection"] = connection

        def enqueue(self, fn: Any, *args: Any) -> _FakeJob:
            return _FakeJob("real-job-1")

    monkeypatch.setattr("src.queue.enqueue.Queue", _FakeRealQueue)
    monkeypatch.setattr(
        "src.queue.enqueue.get_redis_connection", lambda: "fake-connection"
    )

    job_id = enqueue_crawl("configs/target.yaml", queue_name="custom-queue")

    assert job_id == "real-job-1"
    assert captured == {"name": "custom-queue", "connection": "fake-connection"}
