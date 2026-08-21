"""Unit tests for mock-target/security/botd_integration.py."""

from __future__ import annotations

import logging

import pytest
from security.botd_integration import log_botd_report


class _FakeLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str, dict[str, object]]] = []

    def log(self, level: int, msg: str, extra: dict[str, object] | None = None) -> None:
        self.calls.append((level, msg, extra or {}))


def test_flagged_report_logs_at_warning() -> None:
    """Happy path: a report where BotD flagged a bot logs at WARNING."""
    fake_logger = _FakeLogger()

    log_botd_report(fake_logger, {"bot": "headless_chrome"})  # type: ignore[arg-type]

    level, message, extra = fake_logger.calls[0]
    assert level == logging.WARNING
    assert message == "botd.report"
    assert extra["flagged"] is True


def test_clean_report_logs_at_info() -> None:
    fake_logger = _FakeLogger()

    log_botd_report(fake_logger, {"bot": None})  # type: ignore[arg-type]

    level, _message, extra = fake_logger.calls[0]
    assert level == logging.INFO
    assert extra["flagged"] is False


def test_rejects_non_dict_report() -> None:
    """Failure case 1: a malformed (non-dict) report can't be logged meaningfully."""
    with pytest.raises(TypeError, match="report must be a dict"):
        log_botd_report(_FakeLogger(), "not-a-dict")  # type: ignore[arg-type]


def test_missing_bot_key_treated_as_not_flagged() -> None:
    """Failure-adjacent case 2: a report with no 'bot' key at all (not even
    None) must not crash -- treated the same as a clean report."""
    fake_logger = _FakeLogger()

    log_botd_report(fake_logger, {})  # type: ignore[arg-type]

    level, _message, extra = fake_logger.calls[0]
    assert level == logging.INFO
    assert extra["flagged"] is False
