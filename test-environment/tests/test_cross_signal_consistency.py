"""Unit tests for security/cross_signal_consistency.py -- docs/
REQUIREMENTS.md, entry 32 (Phase 3 item 2, redefined).
"""

from __future__ import annotations

import logging

import pytest
from security.cross_signal_consistency import (
    SessionTimingTracker,
    coefficient_of_variation,
    compute_inconsistency_score,
    is_pattern_too_regular,
    log_cross_signal_check,
)


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# --- coefficient_of_variation / is_pattern_too_regular -----------------


def test_coefficient_of_variation_is_none_with_fewer_than_two_samples() -> None:
    assert coefficient_of_variation([]) is None
    assert coefficient_of_variation([1.0]) is None


def test_coefficient_of_variation_is_zero_for_perfectly_regular_intervals() -> None:
    assert coefficient_of_variation([2.0, 2.0, 2.0]) == 0.0


def test_coefficient_of_variation_is_none_for_a_non_positive_mean() -> None:
    assert coefficient_of_variation([0.0, 0.0]) is None


def test_is_pattern_too_regular_true_for_fixed_intervals() -> None:
    assert is_pattern_too_regular([1.0, 1.0, 1.0], min_intervals=3, cv_threshold=0.15) is True


def test_is_pattern_too_regular_false_for_jittered_intervals() -> None:
    assert is_pattern_too_regular([0.3, 1.9, 0.6], min_intervals=3, cv_threshold=0.15) is False


def test_is_pattern_too_regular_false_below_min_intervals() -> None:
    assert is_pattern_too_regular([1.0], min_intervals=3, cv_threshold=0.15) is False


def test_is_pattern_too_regular_rejects_min_intervals_below_two() -> None:
    with pytest.raises(ValueError, match="min_intervals must be >= 2"):
        is_pattern_too_regular([1.0, 1.0], min_intervals=1, cv_threshold=0.15)


def test_is_pattern_too_regular_rejects_non_positive_cv_threshold() -> None:
    with pytest.raises(ValueError, match="cv_threshold must be > 0"):
        is_pattern_too_regular([1.0, 1.0, 1.0], min_intervals=2, cv_threshold=0)


def test_is_pattern_too_regular_false_for_a_non_positive_mean() -> None:
    """Failure-adjacent case: a zero-mean interval sequence (malformed
    input) must not be treated as "perfectly regular" -- mirrors
    structural/trust_score.py's own identical test."""
    assert is_pattern_too_regular([0.0, 0.0, 0.0], min_intervals=2, cv_threshold=0.15) is False


# --- SessionTimingTracker -------------------------------------------------


def test_record_and_check_returns_none_below_min_samples() -> None:
    clock = _FakeClock()
    tracker = SessionTimingTracker(min_interval_samples=3, clock=clock)

    for _ in range(3):
        clock.advance(1.0)
        result = tracker.record_and_check("session-a")

    assert result is None  # exactly min_interval_samples requests: still not > threshold


def test_record_and_check_true_for_a_mechanically_fixed_cadence() -> None:
    clock = _FakeClock()
    tracker = SessionTimingTracker(
        min_interval_samples=3, regularity_cv_threshold=0.15, clock=clock
    )

    result = None
    for _ in range(6):
        clock.advance(1.0)
        result = tracker.record_and_check("session-a")

    assert result is True


def test_record_and_check_false_for_jittered_timing() -> None:
    clock = _FakeClock()
    tracker = SessionTimingTracker(
        min_interval_samples=3, regularity_cv_threshold=0.15, clock=clock
    )

    jittered_deltas = [0.2, 1.9, 0.6, 2.3, 0.4, 1.7]
    result = None
    for delta in jittered_deltas:
        clock.advance(delta)
        result = tracker.record_and_check("session-a")

    assert result is False


def test_record_and_check_sessions_are_tracked_independently() -> None:
    clock = _FakeClock()
    tracker = SessionTimingTracker(min_interval_samples=3, clock=clock)

    for _ in range(6):
        clock.advance(1.0)
        tracker.record_and_check("session-a")

    result_b = tracker.record_and_check("session-b")

    assert result_b is None  # session-b's very first request


def test_record_and_check_evicts_samples_older_than_the_window() -> None:
    """A request far outside the tracking window must not still count
    toward this session's own interval history -- confirmed by driving
    the score back to "not enough samples yet" after a long gap, not
    just by checking the internal deque directly."""
    clock = _FakeClock()
    tracker = SessionTimingTracker(window_seconds=5.0, min_interval_samples=3, clock=clock)

    for _ in range(5):
        clock.advance(1.0)
        tracker.record_and_check("session-a")  # 5 samples, all within the first 5s window

    clock.advance(100.0)  # far past window_seconds -- every prior sample evicted
    result = tracker.record_and_check("session-a")

    assert result is None  # back to "only 1 real sample in the window"


def test_record_and_check_rejects_empty_session_key() -> None:
    tracker = SessionTimingTracker()

    with pytest.raises(ValueError, match="session_key must be non-empty"):
        tracker.record_and_check("")


def test_rejects_non_positive_window_seconds() -> None:
    with pytest.raises(ValueError, match="window_seconds must be > 0"):
        SessionTimingTracker(window_seconds=0)


# --- compute_inconsistency_score ------------------------------------------


def test_all_three_signals_agree_automated_scores_zero() -> None:
    score = compute_inconsistency_score(
        botd_result={"bot": True},
        fingerprint_report={"webglAvailable": False, "viewportConsistent": False},
        timing_is_regular=True,
    )
    assert score == 0


def test_all_three_signals_agree_clean_scores_zero() -> None:
    score = compute_inconsistency_score(
        botd_result={"bot": False},
        fingerprint_report={"webglAvailable": True, "viewportConsistent": True},
        timing_is_regular=False,
    )
    assert score == 0


def test_one_signal_disagrees_scores_two() -> None:
    """botd + fpscanner both say clean, timing says mechanically
    regular -- exactly the user's own example scenario. 2 disagreeing
    pairs: (botd, timing) and (fpscanner, timing); (botd, fpscanner)
    itself agrees."""
    score = compute_inconsistency_score(
        botd_result={"bot": False},
        fingerprint_report={"webglAvailable": True, "viewportConsistent": True},
        timing_is_regular=True,
    )
    assert score == 2


def test_botd_and_fpscanner_disagree_with_each_other() -> None:
    score = compute_inconsistency_score(
        botd_result={"bot": False},
        fingerprint_report={"webglAvailable": False, "viewportConsistent": False},
        timing_is_regular=None,
    )
    assert score == 1  # only one pair available (timing unknown, skipped)


def test_unknown_timing_is_never_treated_as_a_disagreement_by_itself() -> None:
    score_agree = compute_inconsistency_score(
        botd_result={"bot": True},
        fingerprint_report={"webglAvailable": False, "viewportConsistent": True},
        timing_is_regular=None,
    )
    assert score_agree == 0  # botd/fpscanner agree; timing simply not counted


def test_rejects_a_non_dict_botd_result() -> None:
    with pytest.raises(TypeError, match="botd_result must be a dict"):
        compute_inconsistency_score(
            botd_result="not-a-dict",  # type: ignore[arg-type]
            fingerprint_report={},
            timing_is_regular=None,
        )


def test_rejects_a_non_dict_fingerprint_report() -> None:
    with pytest.raises(TypeError, match="report must be a dict"):
        compute_inconsistency_score(
            botd_result={"bot": False},
            fingerprint_report="not-a-dict",  # type: ignore[arg-type]
            timing_is_regular=None,
        )


# --- log_cross_signal_check ------------------------------------------------


def test_log_cross_signal_check_returns_the_score_and_logs_at_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("test.cross_signal")
    with caplog.at_level(logging.INFO, logger="test.cross_signal"):
        score = log_cross_signal_check(
            logger,
            botd_result={"bot": False},
            fingerprint_report={"webglAvailable": True, "viewportConsistent": True},
            timing_is_regular=True,
        )

    assert score == 2
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelno == logging.INFO
    assert record.inconsistency_score == 2  # type: ignore[attr-defined]
