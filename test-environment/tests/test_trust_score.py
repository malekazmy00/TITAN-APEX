"""Unit tests for structural/trust_score.py -- docs/REQUIREMENTS.md,
Phase 3 item 1.
"""

from __future__ import annotations

import pytest
from structural.trust_score import (
    TrustScoreTier,
    TrustScoreTracker,
    coefficient_of_variation,
    is_pattern_too_regular,
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
    assert coefficient_of_variation([1.0, 1.0, 1.0, 1.0]) == 0.0


def test_coefficient_of_variation_is_high_for_irregular_intervals() -> None:
    cv = coefficient_of_variation([0.1, 5.0, 0.2, 4.5])
    assert cv is not None
    assert cv > 0.3


def test_coefficient_of_variation_is_none_for_a_non_positive_mean() -> None:
    """Failure-adjacent case: a zero/negative-mean sequence (malformed
    input, e.g. all-zero intervals) must not be treated as "perfectly
    regular" either -- mirrors
    src/middlewares/rate_limiter.py's own identical test."""
    assert coefficient_of_variation([0.0, 0.0]) is None
    assert coefficient_of_variation([-1.0, -3.0]) is None


def test_is_pattern_too_regular_false_for_a_non_positive_mean() -> None:
    """Same malformed-input case, one level up: is_pattern_too_regular
    must not flag it as regular either (it just falls through to
    coefficient_of_variation returning None)."""
    assert (
        is_pattern_too_regular([0.0, 0.0, 0.0], min_intervals=2, cv_threshold=0.15) is False
    )


def test_is_pattern_too_regular_true_for_fixed_intervals() -> None:
    assert is_pattern_too_regular([1.0, 1.0, 1.0, 1.0], min_intervals=3, cv_threshold=0.15) is True


def test_is_pattern_too_regular_false_for_jittered_intervals() -> None:
    assert (
        is_pattern_too_regular([0.5, 1.8, 0.9, 2.1], min_intervals=3, cv_threshold=0.15) is False
    )


def test_is_pattern_too_regular_false_below_min_intervals() -> None:
    assert is_pattern_too_regular([1.0, 1.0], min_intervals=3, cv_threshold=0.15) is False


def test_is_pattern_too_regular_rejects_min_intervals_below_two() -> None:
    with pytest.raises(ValueError, match="min_intervals must be >= 2"):
        is_pattern_too_regular([1.0, 1.0], min_intervals=1, cv_threshold=0.15)


def test_is_pattern_too_regular_rejects_non_positive_cv_threshold() -> None:
    with pytest.raises(ValueError, match="cv_threshold must be > 0"):
        is_pattern_too_regular([1.0, 1.0, 1.0], min_intervals=2, cv_threshold=0)


# --- TrustScoreTracker ---------------------------------------------------


def test_a_fresh_session_starts_at_zero_and_allowed() -> None:
    clock = _FakeClock()
    tracker = TrustScoreTracker(clock=clock)

    result = tracker.record_request("session-a", user_agent="ua-1", referer=None)

    assert result.score == 0  # missing referer on the *first* request is normal
    assert result.tier is TrustScoreTier.ALLOWED


def test_missing_referer_past_the_first_request_adds_points() -> None:
    clock = _FakeClock()
    tracker = TrustScoreTracker(referer_points=10, clock=clock)

    tracker.record_request("session-a", user_agent="ua-1", referer=None)
    clock.advance(5.0)
    result = tracker.record_request("session-a", user_agent="ua-1", referer=None)

    assert result.score >= 10


def test_a_real_referer_never_adds_referer_points() -> None:
    clock = _FakeClock()
    tracker = TrustScoreTracker(
        referer_points=10, timing_points=0, fingerprint_repeat_threshold=100, clock=clock
    )

    tracker.record_request("session-a", user_agent="ua-1", referer=None)
    for _ in range(5):
        clock.advance(1.0)
        result = tracker.record_request(
            "session-a", user_agent="ua-1", referer="https://example.test/"
        )

    assert result.score == 0


def test_mechanically_regular_timing_adds_points() -> None:
    clock = _FakeClock()
    tracker = TrustScoreTracker(
        timing_points=15,
        min_interval_samples=3,
        regularity_cv_threshold=0.15,
        fingerprint_repeat_threshold=100,
        clock=clock,
    )

    result = None
    for _ in range(6):
        clock.advance(1.0)  # perfectly fixed cadence -- CV == 0
        result = tracker.record_request(
            "session-a", user_agent="ua-1", referer="https://example.test/"
        )

    assert result is not None
    assert result.score >= 15


def test_jittered_timing_never_adds_timing_points() -> None:
    clock = _FakeClock()
    tracker = TrustScoreTracker(
        timing_points=15,
        min_interval_samples=3,
        regularity_cv_threshold=0.15,
        fingerprint_repeat_threshold=100,
        clock=clock,
    )

    jittered_deltas = [0.2, 1.9, 0.6, 2.3, 0.4, 1.7]
    result = None
    for delta in jittered_deltas:
        clock.advance(delta)
        result = tracker.record_request(
            "session-a", user_agent="ua-1", referer="https://example.test/"
        )

    assert result is not None
    assert result.score == 0


def test_repeated_identical_user_agent_past_threshold_adds_points() -> None:
    clock = _FakeClock()
    tracker = TrustScoreTracker(fingerprint_points=20, fingerprint_repeat_threshold=3, clock=clock)

    result = None
    for _ in range(5):
        clock.advance(1.0)
        result = tracker.record_request(
            "session-a", user_agent="same-ua", referer="https://example.test/"
        )

    assert result is not None
    assert result.score >= 20


def test_changing_user_agent_resets_the_fingerprint_streak() -> None:
    clock = _FakeClock()
    tracker = TrustScoreTracker(
        fingerprint_points=20, fingerprint_repeat_threshold=2, timing_points=0, clock=clock
    )

    for ua in ["ua-1", "ua-2", "ua-3", "ua-4"]:
        clock.advance(1.0)
        result = tracker.record_request("session-a", user_agent=ua, referer="https://example.test/")

    assert result.score == 0  # never repeated the same UA twice in a row


def test_score_escalates_through_all_three_tiers_with_worst_case_behavior() -> None:
    """No referer + same UA, repeated many times -- must genuinely walk
    through RATE_LIMITED -> CHALLENGE -> BLOCKED, not jump straight to
    blocked. Point values chosen so the referer signal alone crosses
    RATE_LIMITED then CHALLENGE one request at a time before the
    fingerprint-repeat signal (a later, larger jump) ever fires --
    timing disabled here so this test isolates the *tier-walking*
    behavior itself, not any one signal's own contribution (each
    signal already has its own dedicated test above)."""
    clock = _FakeClock()
    tracker = TrustScoreTracker(
        rate_limit_threshold=10,
        challenge_threshold=30,
        block_threshold=50,
        timing_points=0,
        referer_points=8,
        fingerprint_points=20,
        fingerprint_repeat_threshold=5,
        clock=clock,
    )

    tiers_seen: list[TrustScoreTier] = []
    for _ in range(10):
        clock.advance(1.0)
        result = tracker.record_request("session-a", user_agent="same-ua", referer=None)
        tiers_seen.append(result.tier)

    assert TrustScoreTier.ALLOWED in tiers_seen
    assert TrustScoreTier.RATE_LIMITED in tiers_seen
    assert TrustScoreTier.CHALLENGE in tiers_seen
    assert TrustScoreTier.BLOCKED in tiers_seen
    # Escalation must be monotonic (in tier order), never regress.
    tier_order = {
        TrustScoreTier.ALLOWED: 0,
        TrustScoreTier.RATE_LIMITED: 1,
        TrustScoreTier.CHALLENGE: 2,
        TrustScoreTier.BLOCKED: 3,
    }
    ranks = [tier_order[t] for t in tiers_seen]
    assert ranks == sorted(ranks)


def test_retry_after_is_only_set_for_the_rate_limited_tier() -> None:
    """20 points from the timing signal alone lands squarely inside the
    [rate_limit_threshold, challenge_threshold) band -- unlike an
    overshooting value (e.g. 100), which would jump straight past
    RATE_LIMITED to BLOCKED and never actually exercise this tier."""
    clock = _FakeClock()
    tracker = TrustScoreTracker(
        rate_limit_threshold=15,
        challenge_threshold=35,
        block_threshold=55,
        timing_points=20,
        min_interval_samples=3,
        clock=clock,
    )

    for _ in range(4):
        clock.advance(1.0)
        result = tracker.record_request("session-a", user_agent="ua-1", referer="https://x.test/")

    assert result.score == 20
    assert result.tier is TrustScoreTier.RATE_LIMITED
    assert result.retry_after_seconds is not None
    assert result.retry_after_seconds > 0


def test_score_never_exceeds_one_hundred() -> None:
    clock = _FakeClock()
    tracker = TrustScoreTracker(
        rate_limit_threshold=1,
        challenge_threshold=2,
        block_threshold=3,
        timing_points=50,
        referer_points=50,
        fingerprint_points=50,
        min_interval_samples=2,
        fingerprint_repeat_threshold=1,
        clock=clock,
    )

    result = None
    for _ in range(20):
        clock.advance(1.0)
        result = tracker.record_request("session-a", user_agent="ua-1", referer=None)

    assert result.score <= 100


def test_different_sessions_are_tracked_independently() -> None:
    clock = _FakeClock()
    tracker = TrustScoreTracker(
        timing_points=15, min_interval_samples=3, fingerprint_repeat_threshold=100, clock=clock
    )

    for _ in range(6):
        clock.advance(1.0)
        tracker.record_request("session-a", user_agent="ua-1", referer="https://x.test/")

    result_b = tracker.record_request("session-b", user_agent="ua-1", referer="https://x.test/")

    assert result_b.score == 0
    assert result_b.tier is TrustScoreTier.ALLOWED


def test_record_request_rejects_empty_client_key() -> None:
    tracker = TrustScoreTracker()

    with pytest.raises(ValueError, match="client_key must be non-empty"):
        tracker.record_request("", user_agent="ua-1", referer=None)


def test_rejects_non_positive_rate_limit_threshold() -> None:
    with pytest.raises(ValueError, match="rate_limit_threshold must be > 0"):
        TrustScoreTracker(rate_limit_threshold=0)


def test_rejects_challenge_threshold_not_above_rate_limit_threshold() -> None:
    with pytest.raises(ValueError, match="challenge_threshold must be > rate_limit_threshold"):
        TrustScoreTracker(rate_limit_threshold=30, challenge_threshold=30)


def test_rejects_block_threshold_not_above_challenge_threshold() -> None:
    with pytest.raises(ValueError, match="block_threshold must be > challenge_threshold"):
        TrustScoreTracker(challenge_threshold=60, block_threshold=60)


def test_rejects_non_positive_window_seconds() -> None:
    with pytest.raises(ValueError, match="window_seconds must be > 0"):
        TrustScoreTracker(window_seconds=0)
