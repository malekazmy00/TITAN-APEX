"""Unit tests for mock-target/security/honeypot_logger.py."""

from __future__ import annotations

import logging

import pytest
from security.honeypot_logger import log_honeypot_trigger


class _FakeLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str, dict[str, object]]] = []

    def critical(self, msg: str, extra: dict[str, object] | None = None) -> None:
        self.calls.append((logging.CRITICAL, msg, extra or {}))


def test_logs_at_critical_with_full_context() -> None:
    """Happy path: a honeypot hit is logged CRITICAL with everything needed to
    investigate it."""
    fake_logger = _FakeLogger()

    log_honeypot_trigger(
        fake_logger,  # type: ignore[arg-type]
        token="abc123",
        path="/honeypot-trap/abc123",
        remote_addr="10.0.0.5",
        user_agent="Scrapy/2.11",
    )

    assert len(fake_logger.calls) == 1
    level, message, extra = fake_logger.calls[0]
    assert level == logging.CRITICAL
    assert message == "honeypot.triggered"
    assert extra["token"] == "abc123"
    assert extra["remote_addr"] == "10.0.0.5"


def test_rejects_empty_token() -> None:
    """Failure case 1: no token means no honeypot to attribute the hit to."""
    with pytest.raises(ValueError, match="token must be non-empty"):
        log_honeypot_trigger(_FakeLogger(), token="", path="/x", remote_addr=None, user_agent=None)  # type: ignore[arg-type]


def test_rejects_empty_path() -> None:
    """Failure case 2: no path means nothing meaningful was actually hit."""
    with pytest.raises(ValueError, match="path must be non-empty"):
        log_honeypot_trigger(_FakeLogger(), token="abc", path="", remote_addr=None, user_agent=None)  # type: ignore[arg-type]
