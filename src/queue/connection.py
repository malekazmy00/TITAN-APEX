"""Redis connection factory for the task queue.

Nothing here hardcodes a host or port: the URL comes from
``TITAN_REDIS_URL`` (see ``.env.example``), defaulting to a plain local
Redis for development.
"""

from __future__ import annotations

import os
from collections.abc import Callable

import redis

from src.core.exceptions import QueueError

DEFAULT_REDIS_URL = "redis://localhost:6379/0"

ClientFactory = Callable[[str], redis.Redis]


def get_redis_connection(
    redis_url: str | None = None, client_factory: ClientFactory | None = None
) -> redis.Redis:
    """Build a Redis client and verify it can actually be reached.

    ``client_factory`` is injectable so unit tests can exercise the
    connection-validation logic without a real Redis instance.

    Raises:
        QueueError: if the connection cannot be established (bad URL,
            Redis not running, network failure).
    """
    url = redis_url or os.environ.get("TITAN_REDIS_URL", DEFAULT_REDIS_URL)
    factory = client_factory or redis.Redis.from_url
    try:
        connection = factory(url)
        connection.ping()
    except redis.exceptions.RedisError as exc:
        raise QueueError(f"cannot connect to Redis at {url}: {exc}") from exc
    return connection
