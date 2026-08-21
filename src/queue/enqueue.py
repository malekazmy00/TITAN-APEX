"""Enqueue crawl jobs onto the Redis/RQ queue."""

from __future__ import annotations

from typing import Any, Protocol

from rq import Queue

from src.queue.connection import get_redis_connection
from src.queue.tasks import run_spider_job

DEFAULT_QUEUE_NAME = "crawls"


class _JobLike(Protocol):
    id: str


class _QueueLike(Protocol):
    def enqueue(self, fn: Any, *args: Any) -> _JobLike: ...


def enqueue_crawl(
    config_path: str,
    queue_name: str = DEFAULT_QUEUE_NAME,
    queue: _QueueLike | None = None,
) -> str:
    """Enqueue a crawl job for ``config_path``. Returns the RQ job id.

    ``queue`` is injectable so unit tests never need a real Redis
    connection; when omitted, a real ``rq.Queue`` backed by
    :func:`get_redis_connection` is used.
    """
    target_queue: _QueueLike | Queue = queue or Queue(
        queue_name, connection=get_redis_connection()
    )
    job = target_queue.enqueue(run_spider_job, config_path)
    return str(job.id)
