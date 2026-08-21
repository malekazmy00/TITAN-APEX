"""Unit tests for src/alerting.py."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from src.alerting import AlertDispatcher, AlertEvent, dispatcher_from_settings


def _event() -> AlertEvent:
    return AlertEvent(
        source="circuit_breaker",
        domain="example.com",
        reason="http_503",
        consecutive_failures=5,
        cooldown_seconds=60.0,
        occurred_at=datetime.now(tz=UTC),
    )


def test_send_posts_to_webhook_when_configured() -> None:
    """Happy path: with a webhook configured, the event is POSTed to it."""
    calls: list[tuple[str, dict[str, Any], int]] = []

    def fake_http_post(url: str, payload: dict[str, Any], timeout_ms: int) -> None:
        calls.append((url, payload, timeout_ms))

    dispatcher = AlertDispatcher(
        webhook_url="https://alerts.example.com/hook", http_post=fake_http_post
    )

    dispatcher.send(_event())

    assert len(calls) == 1
    url, payload, _timeout_ms = calls[0]
    assert url == "https://alerts.example.com/hook"
    assert payload["domain"] == "example.com"
    assert payload["consecutive_failures"] == 5


def test_send_without_webhook_only_logs() -> None:
    """Failure case 1 (no failure, but the 'no webhook' branch): no HTTP call is made."""
    calls: list[str] = []

    def fake_http_post(url: str, payload: dict[str, Any], timeout_ms: int) -> None:
        calls.append(url)

    dispatcher = AlertDispatcher(webhook_url=None, http_post=fake_http_post)

    dispatcher.send(_event())

    assert calls == []


def test_send_logs_but_does_not_raise_when_webhook_delivery_fails() -> None:
    """Failure case 2: a broken webhook must never crash the caller."""

    def failing_http_post(url: str, payload: dict[str, Any], timeout_ms: int) -> None:
        raise ConnectionError("connection refused")

    logged: list[str] = []

    class _FakeLogger:
        def critical(self, msg: str, extra: dict[str, object] | None = None) -> None:
            pass

        def error(self, msg: str, extra: dict[str, object] | None = None) -> None:
            logged.append(msg)

    dispatcher = AlertDispatcher(
        webhook_url="https://alerts.example.com/hook",
        http_post=failing_http_post,
        logger=_FakeLogger(),  # type: ignore[arg-type]
    )

    dispatcher.send(_event())  # must not raise

    assert logged == ["alert.webhook_delivery_failed"]


def test_send_always_logs_critical() -> None:
    logged_levels: list[str] = []

    class _FakeLogger:
        def critical(self, msg: str, extra: dict[str, object] | None = None) -> None:
            logged_levels.append("critical")

        def error(self, msg: str, extra: dict[str, object] | None = None) -> None:
            logged_levels.append("error")

    dispatcher = AlertDispatcher(webhook_url=None, logger=_FakeLogger())  # type: ignore[arg-type]

    dispatcher.send(_event())

    assert logged_levels == ["critical"]


def test_dispatcher_from_settings_reads_webhook_url() -> None:
    class _FakeSettings:
        def get(self, name: str, default: object = None) -> object:
            return {"TITAN_ALERT_WEBHOOK_URL": "https://alerts.example.com/hook"}.get(
                name, default
            )

    dispatcher = dispatcher_from_settings(_FakeSettings())

    assert dispatcher._webhook_url == "https://alerts.example.com/hook"


def test_dispatcher_from_settings_defaults_to_no_webhook() -> None:
    class _FakeSettings:
        def get(self, name: str, default: object = None) -> object:
            return default

    dispatcher = dispatcher_from_settings(_FakeSettings())

    assert dispatcher._webhook_url is None


def test_alert_event_rejects_missing_fields() -> None:
    with pytest.raises(ValidationError):
        AlertEvent()  # type: ignore[call-arg]
