"""Unit tests for src/middlewares/rate_limiter.py.

A fake, fully-controllable clock is injected everywhere (same pattern
test_circuit_breaker.py already uses) so no test ever sleeps for real,
and every timing-dependent behavior (window trimming, escalating
cooldowns, the clean-streak reset) is exercised deterministically.
"""

from __future__ import annotations

import pytest
from scrapy.exceptions import IgnoreRequest
from scrapy.http import Request

from src.alerting import AlertEvent
from src.middlewares.rate_limiter import (
    RateLimiterMiddleware,
    RateLimitLevel,
    account_id_key,
    coefficient_of_variation,
    compute_escalated_backoff_seconds,
    default_levels,
    egress_asn_key,
    egress_ip_key,
    is_pattern_too_regular,
    target_domain_key,
)


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> _FakeClock:
    return _FakeClock()


def _request(url: str = "https://example.com/", meta: dict[str, object] | None = None) -> Request:
    return Request(url, meta=meta)


# --- coefficient_of_variation --------------------------------------------


def test_coefficient_of_variation_happy_path_known_values() -> None:
    """Happy path: a hand-computable case (mean=2, population stddev=1 ->
    cv=0.5)."""
    assert coefficient_of_variation([1.0, 3.0]) == pytest.approx(0.5)


def test_coefficient_of_variation_is_zero_for_perfectly_uniform_intervals() -> None:
    assert coefficient_of_variation([10.0, 10.0, 10.0, 10.0]) == pytest.approx(0.0)


def test_coefficient_of_variation_is_none_for_fewer_than_two_samples() -> None:
    """Failure-adjacent case 1: undefined, not zero -- must never be
    mistaken for "perfectly regular"."""
    assert coefficient_of_variation([5.0]) is None
    assert coefficient_of_variation([]) is None


def test_coefficient_of_variation_is_none_for_a_non_positive_mean() -> None:
    """Failure-adjacent case 2: a zero/negative-mean sequence (malformed
    input, e.g. all-zero intervals) must not be treated as "perfectly
    regular" either."""
    assert coefficient_of_variation([0.0, 0.0]) is None
    assert coefficient_of_variation([-1.0, -3.0]) is None


# --- is_pattern_too_regular -----------------------------------------------


def test_is_pattern_too_regular_flags_a_fixed_cadence() -> None:
    intervals = [5.0, 5.0, 5.0, 5.0, 5.0]
    assert is_pattern_too_regular(intervals, min_intervals=5, cv_threshold=0.15) is True


def test_is_pattern_too_regular_allows_natural_jitter() -> None:
    intervals = [3.2, 5.8, 1.1, 6.7, 4.0]
    assert is_pattern_too_regular(intervals, min_intervals=5, cv_threshold=0.15) is False


def test_is_pattern_too_regular_ignores_too_few_samples() -> None:
    """Failure-adjacent case: a perfectly uniform pair alone must not
    trigger before min_intervals is actually reached -- too little
    evidence to judge yet."""
    intervals = [5.0, 5.0]
    assert is_pattern_too_regular(intervals, min_intervals=5, cv_threshold=0.15) is False


def test_is_pattern_too_regular_rejects_invalid_min_intervals() -> None:
    with pytest.raises(ValueError, match="min_intervals must be >= 2"):
        is_pattern_too_regular([1.0, 2.0], min_intervals=1, cv_threshold=0.1)


def test_is_pattern_too_regular_rejects_invalid_cv_threshold() -> None:
    with pytest.raises(ValueError, match="cv_threshold must be > 0"):
        is_pattern_too_regular([1.0, 2.0], min_intervals=2, cv_threshold=0.0)


# --- compute_escalated_backoff_seconds ------------------------------------


def test_compute_escalated_backoff_seconds_doubles_each_step() -> None:
    assert compute_escalated_backoff_seconds(1, base_seconds=10.0, max_seconds=1000.0) == 10.0
    assert compute_escalated_backoff_seconds(2, base_seconds=10.0, max_seconds=1000.0) == 20.0
    assert compute_escalated_backoff_seconds(3, base_seconds=10.0, max_seconds=1000.0) == 40.0


def test_compute_escalated_backoff_seconds_caps_at_max() -> None:
    assert compute_escalated_backoff_seconds(10, base_seconds=10.0, max_seconds=100.0) == 100.0


def test_compute_escalated_backoff_seconds_rejects_a_non_positive_step() -> None:
    with pytest.raises(ValueError, match="escalation_step must be >= 1"):
        compute_escalated_backoff_seconds(0, base_seconds=10.0, max_seconds=100.0)


# --- key functions ---------------------------------------------------------


def test_target_domain_key_extracts_the_netloc() -> None:
    assert target_domain_key(_request("https://example.com/feed")) == "example.com"


def test_account_id_key_is_none_when_unset() -> None:
    assert account_id_key(_request()) is None


def test_account_id_key_reads_meta_when_present() -> None:
    assert account_id_key(_request(meta={"account_id": "acct-1"})) == "acct-1"


def test_egress_ip_key_is_none_when_unset() -> None:
    assert egress_ip_key(_request()) is None


def test_egress_ip_key_reads_meta_when_present() -> None:
    assert egress_ip_key(_request(meta={"egress_ip": "203.0.113.5"})) == "203.0.113.5"


def test_egress_asn_key_is_none_when_unset() -> None:
    assert egress_asn_key(_request()) is None


def test_egress_asn_key_reads_meta_when_present() -> None:
    assert egress_asn_key(_request(meta={"egress_asn": "AS64500"})) == "AS64500"


def test_default_levels_has_the_four_documented_levels() -> None:
    names = [level.name for level in default_levels()]
    assert names == ["target", "account", "ip", "asn_subnet"]


# --- RateLimitLevel validation ---------------------------------------------


def test_rate_limit_level_rejects_a_non_positive_max_requests() -> None:
    with pytest.raises(ValueError, match="max_requests must be >= 1"):
        RateLimitLevel("target", target_domain_key, max_requests=0, window_seconds=60.0)


def test_rate_limit_level_rejects_a_non_positive_window() -> None:
    with pytest.raises(ValueError, match="window_seconds must be > 0"):
        RateLimitLevel("target", target_domain_key, max_requests=10, window_seconds=0.0)


# --- RateLimiterMiddleware: constructor validation -------------------------


def test_rejects_invalid_regularity_min_intervals() -> None:
    with pytest.raises(ValueError, match="regularity_min_intervals must be >= 2"):
        RateLimiterMiddleware(regularity_min_intervals=1)


def test_rejects_invalid_regularity_cv_threshold() -> None:
    with pytest.raises(ValueError, match="regularity_cv_threshold must be > 0"):
        RateLimiterMiddleware(regularity_cv_threshold=0.0)


def test_rejects_invalid_violation_threshold() -> None:
    with pytest.raises(ValueError, match="violation_threshold must be >= 1"):
        RateLimiterMiddleware(violation_threshold=0)


def test_rejects_invalid_backoff_base_seconds() -> None:
    with pytest.raises(ValueError, match="backoff_base_seconds must be > 0"):
        RateLimiterMiddleware(backoff_base_seconds=0.0)


def test_rejects_a_backoff_max_below_the_base() -> None:
    with pytest.raises(ValueError, match="backoff_max_seconds"):
        RateLimiterMiddleware(backoff_base_seconds=100.0, backoff_max_seconds=10.0)


def test_rejects_invalid_violation_reset_seconds() -> None:
    with pytest.raises(ValueError, match="violation_reset_seconds must be > 0"):
        RateLimiterMiddleware(violation_reset_seconds=0.0)


# --- RateLimiterMiddleware: behavior ----------------------------------------


def _single_level_middleware(
    clock: _FakeClock,
    max_requests: int = 1,
    window_seconds: float = 1000.0,
    violation_threshold: int = 2,
    backoff_base_seconds: float = 10.0,
    backoff_max_seconds: float = 1000.0,
    violation_reset_seconds: float = 300.0,
    # High on purpose in most fixtures: isolates count-based behavior from
    # the separate pattern-detection mechanism, tested on its own below.
    regularity_min_intervals: int = 1000,
    alert_dispatcher: object | None = None,
) -> RateLimiterMiddleware:
    level = RateLimitLevel("target", target_domain_key, max_requests, window_seconds)
    return RateLimiterMiddleware(
        levels=[level],
        clock=clock,
        violation_threshold=violation_threshold,
        backoff_base_seconds=backoff_base_seconds,
        backoff_max_seconds=backoff_max_seconds,
        violation_reset_seconds=violation_reset_seconds,
        regularity_min_intervals=regularity_min_intervals,
        alert_dispatcher=alert_dispatcher,  # type: ignore[arg-type]
    )


def test_clean_requests_under_the_limit_are_never_blocked(clock: _FakeClock) -> None:
    middleware = _single_level_middleware(clock, max_requests=5)
    for _ in range(5):
        assert middleware.process_request(_request(), spider=object()) is None


def test_violations_below_the_threshold_are_soft_and_still_allowed(clock: _FakeClock) -> None:
    """Failure-adjacent case: a single overage (or two, under
    violation_threshold=2) must log a warning but not drop the request --
    "don't overreact to one blip", same shape CircuitBreakerMiddleware
    already has for HTTP failures."""
    middleware = _single_level_middleware(clock, max_requests=1, violation_threshold=3)

    assert middleware.process_request(_request(), spider=object()) is None  # request_times=[0]
    # Second request exceeds max_requests=1 -> violation 1 of 3 -- soft.
    assert middleware.process_request(_request(), spider=object()) is None


def test_violation_threshold_reached_blocks_and_raises(clock: _FakeClock) -> None:
    middleware = _single_level_middleware(clock, max_requests=1, violation_threshold=2)

    middleware.process_request(_request(), spider=object())  # clean
    middleware.process_request(_request(), spider=object())  # violation 1/2, soft

    with pytest.raises(IgnoreRequest, match="blocked for"):
        middleware.process_request(_request(), spider=object())  # violation 2/2, hard


def test_blocked_scope_stays_blocked_until_cooldown_elapses(clock: _FakeClock) -> None:
    middleware = _single_level_middleware(
        clock, max_requests=1, violation_threshold=2, backoff_base_seconds=10.0
    )
    middleware.process_request(_request(), spider=object())
    middleware.process_request(_request(), spider=object())
    with pytest.raises(IgnoreRequest):
        middleware.process_request(_request(), spider=object())  # blocked_until = 10.0

    clock.advance(9.9)
    with pytest.raises(IgnoreRequest, match="still in cooldown"):
        middleware.process_request(_request(), spider=object())

    clock.advance(0.2)  # now 10.1s -- cooldown elapsed
    # The scope is reachable again -- it may itself immediately violate
    # again (still over its own count), but that's a *fresh* evaluation,
    # not the old "still in cooldown" rejection.
    with pytest.raises(IgnoreRequest, match="blocked for"):
        middleware.process_request(_request(), spider=object())


def test_repeated_blocks_escalate_the_cooldown_duration(clock: _FakeClock) -> None:
    middleware = _single_level_middleware(
        clock,
        max_requests=1,
        violation_threshold=2,
        backoff_base_seconds=10.0,
        backoff_max_seconds=1000.0,
    )
    middleware.process_request(_request(), spider=object())
    middleware.process_request(_request(), spider=object())
    with pytest.raises(IgnoreRequest):
        middleware.process_request(_request(), spider=object())  # 1st block: 10s

    scope = middleware._scopes[("target", "example.com")]
    assert scope.blocked_until == pytest.approx(10.0)

    clock.advance(10.0)  # exactly at the boundary -- no longer in cooldown
    with pytest.raises(IgnoreRequest):
        middleware.process_request(_request(), spider=object())  # 2nd block: 20s (doubled)

    assert scope.blocked_until == pytest.approx(30.0)  # 10.0 (now) + 20.0


def test_violation_count_resets_after_a_clean_streak(clock: _FakeClock) -> None:
    middleware = _single_level_middleware(
        clock, max_requests=1, violation_threshold=5, violation_reset_seconds=100.0
    )
    middleware.process_request(_request(), spider=object())  # clean, request_times=[0]
    middleware.process_request(_request(), spider=object())  # violation 1/5

    scope = middleware._scopes[("target", "example.com")]
    assert scope.violation_count == 1

    # Advance far enough that the window (1000s) still holds the old
    # timestamps (so this would still violate on count alone) but the
    # violation-reset clock (100s) has elapsed since the last violation.
    clock.advance(150.0)
    middleware.process_request(_request(), spider=object())

    # A fresh violation happened again (still over max_requests=1), but
    # the *streak* was reset first -- so this is violation 1 of 5 again,
    # not 2.
    assert scope.violation_count == 1


def test_a_dropped_request_never_pollutes_another_levels_window(clock: _FakeClock) -> None:
    """Correctness detail from the module's own docstring: a request
    hard-blocked by one level must not have its timestamp recorded
    against any *other* level it was also evaluated against."""
    target_level = RateLimitLevel(
        "target", target_domain_key, max_requests=1000, window_seconds=1000.0
    )
    account_level = RateLimitLevel(
        "account", account_id_key, max_requests=1, window_seconds=1000.0
    )
    middleware = RateLimiterMiddleware(
        levels=[target_level, account_level],
        clock=clock,
        violation_threshold=1,  # block on the very first violation
        regularity_min_intervals=1000,
    )
    meta = {"account_id": "acct-1"}

    middleware.process_request(_request(meta=meta), spider=object())  # both levels clean

    with pytest.raises(IgnoreRequest):
        # account level is now over its own max_requests=1 -> hard block
        # (violation_threshold=1) -- target level would have been clean
        # on its own (1000 headroom), but the whole request is dropped.
        middleware.process_request(_request(meta=meta), spider=object())

    target_scope = middleware._scopes[("target", "example.com")]
    assert len(target_scope.request_times) == 1  # only the first, successful request


def test_pattern_detected_counts_as_a_violation(clock: _FakeClock) -> None:
    middleware = _single_level_middleware(
        clock,
        max_requests=1000,  # generous -- isolates pattern detection alone
        violation_threshold=1,
        regularity_min_intervals=2,
    )
    middleware.process_request(_request(), spider=object())  # timestamps=[0]
    clock.advance(10.0)
    middleware.process_request(_request(), spider=object())  # timestamps=[0, 10]
    clock.advance(10.0)
    # Third request: this round's would-be sequence is [0, 10, 20] -> two
    # perfectly uniform 10.0s intervals -> cv=0, and regularity_min_intervals
    # =2 means that's already enough samples to judge -> flagged.
    with pytest.raises(IgnoreRequest, match="pattern_detected"):
        middleware.process_request(_request(), spider=object())


def test_natural_jitter_never_triggers_pattern_detection(clock: _FakeClock) -> None:
    middleware = _single_level_middleware(
        clock, max_requests=1000, violation_threshold=1, regularity_min_intervals=3
    )
    for delay in (3.2, 5.8, 1.1, 6.7):
        clock.advance(delay)
        assert middleware.process_request(_request(), spider=object()) is None


def test_levels_with_no_applicable_key_are_silently_skipped(clock: _FakeClock) -> None:
    """account/ip/asn_subnet with no meta set at all -- this project's
    current reality for every existing spider/config."""
    middleware = RateLimiterMiddleware(clock=clock)  # default_levels()
    # Comfortably under the target level's own default max_requests=30 --
    # this only asserts that the 3 meta-driven levels never raise just
    # because their key is always None, not the target level's own
    # count-based behavior (covered separately above).
    for _ in range(10):
        assert middleware.process_request(_request(), spider=object()) is None


def test_scopes_are_tracked_independently_per_key(clock: _FakeClock) -> None:
    middleware = _single_level_middleware(clock, max_requests=1, violation_threshold=1)

    middleware.process_request(_request("https://bad.example.com/"), spider=object())
    with pytest.raises(IgnoreRequest):
        middleware.process_request(_request("https://bad.example.com/"), spider=object())

    # A different domain (different scope key) is unaffected.
    good_result = middleware.process_request(_request("https://good.example.com/"), spider=object())
    assert good_result is None


def test_alert_dispatched_on_hard_block(clock: _FakeClock) -> None:
    sent_events: list[AlertEvent] = []

    class _FakeDispatcher:
        def send(self, event: AlertEvent) -> None:
            sent_events.append(event)

    middleware = _single_level_middleware(
        clock, max_requests=1, violation_threshold=1, alert_dispatcher=_FakeDispatcher()
    )
    middleware.process_request(_request(), spider=object())  # clean
    with pytest.raises(IgnoreRequest):
        middleware.process_request(_request(), spider=object())  # hard block, threshold=1

    assert len(sent_events) == 1
    event = sent_events[0]
    assert event.source == "rate_limiter"
    assert event.domain == "target:example.com"
    assert event.reason == "count_exceeded"
    assert event.consecutive_failures == 1


def test_clean_requests_never_dispatch_an_alert(clock: _FakeClock) -> None:
    sent_events: list[AlertEvent] = []

    class _FakeDispatcher:
        def send(self, event: AlertEvent) -> None:
            sent_events.append(event)

    middleware = _single_level_middleware(
        clock, max_requests=10, alert_dispatcher=_FakeDispatcher()
    )
    for _ in range(10):
        middleware.process_request(_request(), spider=object())

    assert sent_events == []


def test_window_trimming_lets_old_scopes_recover(clock: _FakeClock) -> None:
    """Happy path: once old timestamps fall outside window_seconds, they
    no longer count against the limit -- a rate limiter that never
    forgets would never let a well-behaved scope recover."""
    middleware = _single_level_middleware(clock, max_requests=1, window_seconds=60.0)
    middleware.process_request(_request(), spider=object())  # request_times=[0]

    clock.advance(61.0)  # outside the 60s window now
    # Trimmed before the count check -- back under the limit.
    assert middleware.process_request(_request(), spider=object()) is None


def test_from_crawler_reads_settings() -> None:
    values: dict[str, float] = {
        "TITAN_RATE_LIMIT_TARGET_MAX_REQUESTS": 7,
        "TITAN_RATE_LIMIT_TARGET_WINDOW_SECONDS": 12.0,
        "TITAN_RATE_LIMIT_VIOLATION_THRESHOLD": 2,
        "TITAN_RATE_LIMIT_BACKOFF_BASE_SECONDS": 5.0,
    }

    class _FakeSettings:
        def getint(self, name: str, default: int) -> int:
            return int(values.get(name, default))

        def getfloat(self, name: str, default: float) -> float:
            return float(values.get(name, default))

        def get(self, name: str, default: object = None) -> object:
            return default

    class _FakeCrawler:
        settings = _FakeSettings()

    built = RateLimiterMiddleware.from_crawler(_FakeCrawler())

    target = next(level for level in built.levels if level.name == "target")
    assert target.max_requests == 7
    assert target.window_seconds == 12.0
    assert built.violation_threshold == 2
    assert built.backoff_base_seconds == 5.0
