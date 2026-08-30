"""Unit tests for mock-target/security/fpscanner_integration.py."""

from __future__ import annotations

import pytest
from security.fpscanner_integration import log_fingerprint_report, score_fingerprint_report


class _FakeLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def info(self, msg: str, extra: dict[str, object] | None = None) -> None:
        self.calls.append((msg, extra or {}))


def test_score_is_zero_when_both_signals_look_real() -> None:
    """Happy path: a real browser's own values never contribute a point."""
    score = score_fingerprint_report({"webglAvailable": True, "viewportConsistent": True})

    assert score == 0


def test_score_counts_each_independent_signal_separately() -> None:
    """The actual point of a score, not a verdict: each signal
    contributes independently, so two firing signals score higher than
    one -- never a single boolean short-circuit."""
    one_signal = score_fingerprint_report({"webglAvailable": False, "viewportConsistent": True})
    both_signals = score_fingerprint_report({"webglAvailable": False, "viewportConsistent": False})

    assert one_signal == 1
    assert both_signals == 2


def test_missing_keys_treated_as_not_firing() -> None:
    """Failure-adjacent case: a report missing a key entirely (not even
    False) must not crash, and must not count as that signal firing --
    only an explicit False (webglAvailable: False, e.g. a null WebGL
    context) counts."""
    score = score_fingerprint_report({})

    assert score == 0


def test_rejects_non_dict_report() -> None:
    """Failure case: a malformed (non-dict) report can't be scored meaningfully."""
    with pytest.raises(TypeError, match="report must be a dict"):
        score_fingerprint_report("not-a-dict")  # type: ignore[arg-type]


def test_log_fingerprint_report_always_logs_at_info() -> None:
    """docs/REQUIREMENTS.md section 9 entry 19: deliberately never
    WARNING/ERROR yet, regardless of score -- no enforcement threshold
    has been decided (this module's own docstring), unlike BotD's own
    log_botd_report, which already does escalate to WARNING."""
    fake_logger = _FakeLogger()

    log_fingerprint_report(fake_logger, {"webglAvailable": False, "viewportConsistent": False})  # type: ignore[arg-type]

    message, extra = fake_logger.calls[0]
    assert message == "fingerprint.report"
    assert extra["score"] == 2
    assert extra["report"] == {"webglAvailable": False, "viewportConsistent": False}


def test_log_fingerprint_report_rejects_non_dict_report() -> None:
    """Failure case: propagated from score_fingerprint_report, not
    swallowed."""
    with pytest.raises(TypeError, match="report must be a dict"):
        log_fingerprint_report(_FakeLogger(), "not-a-dict")  # type: ignore[arg-type]
