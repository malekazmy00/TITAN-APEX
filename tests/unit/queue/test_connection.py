"""Unit tests for src/queue/connection.py.

The Redis client factory is always injected: these tests never touch a
real Redis instance.
"""

from __future__ import annotations

import pytest
import redis

from src.core.exceptions import QueueError
from src.queue.connection import get_redis_connection


class _FakeWorkingClient:
    def ping(self) -> bool:
        return True


class _FakeBrokenClient:
    def ping(self) -> bool:
        raise redis.exceptions.ConnectionError("connection refused")


def test_get_redis_connection_returns_a_verified_client() -> None:
    """Happy path: a reachable Redis returns the client, ping() succeeded."""
    connection = get_redis_connection(
        redis_url="redis://localhost:6379/0",
        client_factory=lambda url: _FakeWorkingClient(),  # type: ignore[arg-type,return-value]
    )

    assert connection.ping() is True


def test_get_redis_connection_raises_queue_error_when_unreachable() -> None:
    """Failure case 1: a connection failure is wrapped as QueueError, not raw."""
    with pytest.raises(QueueError, match="cannot connect to Redis"):
        get_redis_connection(
            redis_url="redis://localhost:6379/0",
            client_factory=lambda url: _FakeBrokenClient(),  # type: ignore[arg-type,return-value]
        )


def test_get_redis_connection_raises_queue_error_when_factory_itself_fails() -> None:
    """Failure case 2: a malformed URL rejected by the client constructor itself."""

    def failing_factory(url: str) -> redis.Redis:
        raise redis.exceptions.RedisError(f"invalid URL: {url}")

    with pytest.raises(QueueError, match="cannot connect to Redis"):
        get_redis_connection(redis_url="not-a-valid-url", client_factory=failing_factory)


def test_get_redis_connection_uses_env_var_when_url_not_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TITAN_REDIS_URL", "redis://custom-host:6380/1")
    seen_urls: list[str] = []

    def capturing_factory(url: str) -> redis.Redis:
        seen_urls.append(url)
        return _FakeWorkingClient()  # type: ignore[return-value]

    get_redis_connection(client_factory=capturing_factory)

    assert seen_urls == ["redis://custom-host:6380/1"]
