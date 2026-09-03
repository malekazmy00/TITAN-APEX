"""Unit tests for src/middlewares/circuit_breaker.py.

A fake, fully-controllable clock is injected everywhere so no test ever
sleeps for real, and the 60s cooldown is exercised deterministically.
"""

from __future__ import annotations

import pytest
from scrapy.exceptions import IgnoreRequest
from scrapy.http import Request, Response

from src.alerting import AlertEvent
from src.diagnostics.failure_taxonomy import FailureCategory, FailureRecord
from src.middlewares.circuit_breaker import CircuitBreakerMiddleware
from src.response_classifier import ResponsePattern, ResponseStrategy
from src.strategy.strategy_capability import StrategyEngineConfig, StrategyMode
from src.strategy.strategy_engine import StrategyEngine


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


@pytest.fixture
def middleware(clock: _FakeClock) -> CircuitBreakerMiddleware:
    return CircuitBreakerMiddleware(failure_threshold=5, cooldown_seconds=60.0, clock=clock)


def _request(url: str = "https://example.com/") -> Request:
    return Request(url)


def _response(url: str, status: int) -> Response:
    return Response(url=url, status=status, request=_request(url))


def _response_with(
    url: str, status: int, headers: dict[str, str] | None = None, body: bytes = b""
) -> Response:
    return Response(
        url=url, status=status, headers=headers or {}, body=body, request=_request(url)
    )


def test_successful_responses_never_open_the_circuit(middleware: CircuitBreakerMiddleware) -> None:
    """Happy path: repeated 200s keep the circuit closed and never block anything."""
    url = "https://example.com/"
    for _ in range(10):
        assert middleware.process_request(_request(url), spider=object()) is None
        middleware.process_response(_request(url), _response(url, 200), spider=object())

    domain_circuit = middleware._circuits["example.com"]
    assert domain_circuit.consecutive_failures == 0


def test_circuit_opens_after_exactly_the_failure_threshold(
    middleware: CircuitBreakerMiddleware,
) -> None:
    """Failure case 1: the 5th consecutive failure opens the circuit; the 6th request is
    blocked immediately (IgnoreRequest) with zero network call."""
    url = "https://example.com/"
    for _ in range(5):
        req = _request(url)
        assert middleware.process_request(req, spider=object()) is None
        middleware.process_response(req, _response(url, 503), spider=object())

    assert middleware._circuits["example.com"].consecutive_failures == 5

    with pytest.raises(IgnoreRequest, match="is open"):
        middleware.process_request(_request(url), spider=object())


def test_circuit_stays_open_until_cooldown_elapses(
    middleware: CircuitBreakerMiddleware, clock: _FakeClock
) -> None:
    """Failure case 2: the circuit must NOT close before the full cooldown has passed."""
    url = "https://example.com/"
    for _ in range(5):
        req = _request(url)
        middleware.process_request(req, spider=object())
        middleware.process_response(req, _response(url, 503), spider=object())

    clock.advance(59.9)
    with pytest.raises(IgnoreRequest):
        middleware.process_request(_request(url), spider=object())

    clock.advance(0.2)  # now 60.1s since opening
    # Cooldown elapsed: the next request is let through as a half-open trial.
    assert middleware.process_request(_request(url), spider=object()) is None


def test_half_open_trial_success_closes_the_circuit(
    middleware: CircuitBreakerMiddleware, clock: _FakeClock
) -> None:
    url = "https://example.com/"
    for _ in range(5):
        req = _request(url)
        middleware.process_request(req, spider=object())
        middleware.process_response(req, _response(url, 503), spider=object())

    clock.advance(60.0)
    trial_request = _request(url)
    assert middleware.process_request(trial_request, spider=object()) is None
    middleware.process_response(trial_request, _response(url, 200), spider=object())

    # Circuit closed: a fresh request goes straight through, no IgnoreRequest.
    assert middleware.process_request(_request(url), spider=object()) is None
    assert middleware._circuits["example.com"].consecutive_failures == 0


def test_half_open_trial_failure_reopens_for_a_full_new_cooldown(
    middleware: CircuitBreakerMiddleware, clock: _FakeClock
) -> None:
    url = "https://example.com/"
    for _ in range(5):
        req = _request(url)
        middleware.process_request(req, spider=object())
        middleware.process_response(req, _response(url, 503), spider=object())

    clock.advance(60.0)
    trial_request = _request(url)
    middleware.process_request(trial_request, spider=object())
    middleware.process_response(trial_request, _response(url, 503), spider=object())

    # Reopened: blocked again immediately, even though 60s already passed once.
    with pytest.raises(IgnoreRequest):
        middleware.process_request(_request(url), spider=object())


def test_process_exception_counts_as_a_failure(middleware: CircuitBreakerMiddleware) -> None:
    url = "https://nonexistent.invalid/"
    for _ in range(5):
        req = _request(url)
        middleware.process_request(req, spider=object())
        middleware.process_exception(req, ConnectionError("dns failure"), spider=object())

    with pytest.raises(IgnoreRequest):
        middleware.process_request(_request(url), spider=object())


def test_domains_are_tracked_independently(middleware: CircuitBreakerMiddleware) -> None:
    bad_url = "https://bad.example.com/"
    good_url = "https://good.example.com/"
    for _ in range(5):
        req = _request(bad_url)
        middleware.process_request(req, spider=object())
        middleware.process_response(req, _response(bad_url, 503), spider=object())

    with pytest.raises(IgnoreRequest):
        middleware.process_request(_request(bad_url), spider=object())

    # A different domain is unaffected.
    result = middleware.process_request(_request(good_url), spider=object())
    assert result is None


def test_invalid_failure_threshold_raises_value_error() -> None:
    with pytest.raises(ValueError, match="failure_threshold must be >= 1"):
        CircuitBreakerMiddleware(failure_threshold=0)


def test_invalid_cooldown_raises_value_error() -> None:
    with pytest.raises(ValueError, match="cooldown_seconds must be > 0"):
        CircuitBreakerMiddleware(cooldown_seconds=0)


def test_from_crawler_reads_settings() -> None:
    class _FakeSettings:
        def getint(self, name: str, default: int) -> int:
            return {"TITAN_CIRCUIT_FAILURE_THRESHOLD": 3}.get(name, default)

        def getfloat(self, name: str, default: float) -> float:
            return {"TITAN_CIRCUIT_COOLDOWN_SECONDS": 10.0}.get(name, default)

        def getbool(self, name: str, default: bool) -> bool:
            return default

        def get(self, name: str, default: object = None) -> object:
            return default

    class _FakeCrawler:
        settings = _FakeSettings()

    built = CircuitBreakerMiddleware.from_crawler(_FakeCrawler())

    assert built.failure_threshold == 3
    assert built.cooldown_seconds == 10.0


def test_opening_the_circuit_dispatches_an_alert(clock: _FakeClock) -> None:
    """Phase 4: repeated failure (the circuit opening) must trigger an alert."""
    sent_events: list[AlertEvent] = []

    class _FakeDispatcher:
        def send(self, event: AlertEvent) -> None:
            sent_events.append(event)

    middleware = CircuitBreakerMiddleware(
        failure_threshold=5,
        cooldown_seconds=60.0,
        clock=clock,
        alert_dispatcher=_FakeDispatcher(),  # type: ignore[arg-type]
    )
    url = "https://example.com/"
    for _ in range(4):
        req = _request(url)
        middleware.process_request(req, spider=object())
        middleware.process_response(req, _response(url, 503), spider=object())

    assert sent_events == []  # not yet at threshold

    req = _request(url)
    middleware.process_request(req, spider=object())
    middleware.process_response(req, _response(url, 503), spider=object())

    assert len(sent_events) == 1
    event = sent_events[0]
    assert event.source == "circuit_breaker"
    assert event.domain == "example.com"
    assert event.consecutive_failures == 5
    assert event.cooldown_seconds == 60.0


def test_successful_responses_never_dispatch_an_alert(clock: _FakeClock) -> None:
    sent_events: list[AlertEvent] = []

    class _FakeDispatcher:
        def send(self, event: AlertEvent) -> None:
            sent_events.append(event)

    middleware = CircuitBreakerMiddleware(
        failure_threshold=5,
        cooldown_seconds=60.0,
        clock=clock,
        alert_dispatcher=_FakeDispatcher(),  # type: ignore[arg-type]
    )
    url = "https://example.com/"
    for _ in range(10):
        req = _request(url)
        middleware.process_request(req, spider=object())
        middleware.process_response(req, _response(url, 200), spider=object())

    assert sent_events == []


def test_opening_the_circuit_records_a_failure_registry_entry(
    clock: _FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """docs/REQUIREMENTS.md section 9 entry 28 (unified failure
    taxonomy): the "open" event -- the user's own named diagnostic
    ("Circuit Breaker open/close events") -- must be classified and
    recorded, always as network-infra-transient (a circuit only ever
    opens on a real HTTP 5xx or a request-level exception, never a
    target's own anti-bot check this middleware can't see)."""
    recorded: list[FailureRecord] = []
    monkeypatch.setattr(
        "src.middlewares.circuit_breaker.record_failure",
        lambda record, path=None: recorded.append(record),
    )
    middleware = CircuitBreakerMiddleware(failure_threshold=5, cooldown_seconds=60.0, clock=clock)
    url = "https://example.com/"
    for _ in range(5):
        req = _request(url)
        middleware.process_request(req, spider=object())
        middleware.process_response(req, _response(url, 503), spider=object())

    assert len(recorded) == 1
    record = recorded[0]
    assert record.target == "example.com"
    assert record.failure_category is FailureCategory.NETWORK_INFRA_TRANSIENT
    assert record.source == "circuit_breaker.opened"
    assert record.raw_signal["consecutive_failures"] == 5
    assert record.raw_signal["reason"] == "http_503"


def test_below_threshold_failures_do_not_record_anything(
    clock: _FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure-adjacent case: only the actual "open" transition is
    recorded, not every contributing failure below threshold -- avoids
    turning one circuit-open event into a burst of near-duplicate rows."""
    recorded: list[FailureRecord] = []
    monkeypatch.setattr(
        "src.middlewares.circuit_breaker.record_failure",
        lambda record, path=None: recorded.append(record),
    )
    middleware = CircuitBreakerMiddleware(failure_threshold=5, cooldown_seconds=60.0, clock=clock)
    url = "https://example.com/"
    for _ in range(4):
        req = _request(url)
        middleware.process_request(req, spider=object())
        middleware.process_response(req, _response(url, 503), spider=object())

    assert recorded == []


# docs/REQUIREMENTS.md section 9 entry 29 ("الطبقة 2" -- Protection
# Classifier): CLASSIFIABLE_STATUSES (401/403/407/429) responses now get
# classified via src.response_classifier and handled per-pattern instead
# of the plain http_<status> failure counting FAILURE_STATUSES still
# uses.


def test_silent_block_opens_the_circuit_immediately_with_the_long_cooldown(
    clock: _FakeClock,
) -> None:
    """ResponseStrategy.IMMEDIATE_LONG_BACKOFF (docs/REQUIREMENTS.md's
    own Layer 2 spec: "نمط 'فاضي بلا علامة' = backoff طويل فورًا") --
    a single empty-body, no-known-header 403 opens the circuit right
    away, well below the normal failure_threshold, and the next request
    is blocked for silent_block_cooldown_seconds, not cooldown_seconds."""
    middleware = CircuitBreakerMiddleware(
        failure_threshold=5,
        cooldown_seconds=60.0,
        silent_block_cooldown_seconds=300.0,
        clock=clock,
    )
    url = "https://example.com/"
    req = _request(url)
    middleware.process_request(req, spider=object())
    result = middleware.process_response(req, _response_with(url, 403), spider=object())
    assert result.status == 403

    circuit = middleware._circuits["example.com"]
    assert circuit.consecutive_failures == 1  # nowhere near failure_threshold=5

    # Blocked well past the *normal* 60s cooldown -- proves the long
    # cooldown, not the default, is what's actually governing here.
    clock.advance(65.0)
    with pytest.raises(IgnoreRequest, match="is open"):
        middleware.process_request(_request(url), spider=object())

    # But released once the *long* cooldown genuinely elapses.
    clock.advance(240.0)  # total 305s > 300s
    assert middleware.process_request(_request(url), spider=object()) is None


def test_silent_block_cooldown_override_resets_to_normal_after_a_close(
    clock: _FakeClock,
) -> None:
    """A domain that recovers (half-open trial succeeds) must not keep
    an extended cooldown from a previous silent-block open -- a later,
    ordinary threshold-triggered open on the same domain uses the plain
    cooldown_seconds again, not a stale override."""
    middleware = CircuitBreakerMiddleware(
        failure_threshold=5,
        cooldown_seconds=60.0,
        silent_block_cooldown_seconds=300.0,
        clock=clock,
    )
    url = "https://example.com/"
    req = _request(url)
    middleware.process_request(req, spider=object())
    middleware.process_response(req, _response_with(url, 403), spider=object())
    assert middleware._circuits["example.com"].cooldown_override == 300.0

    clock.advance(300.0)
    trial = _request(url)
    middleware.process_request(trial, spider=object())
    middleware.process_response(trial, _response(url, 200), spider=object())
    assert middleware._circuits["example.com"].cooldown_override is None

    for _ in range(5):
        req = _request(url)
        middleware.process_request(req, spider=object())
        middleware.process_response(req, _response(url, 503), spider=object())
    assert middleware._circuits["example.com"].cooldown_override is None

    clock.advance(65.0)  # past the plain 60s cooldown, well under 300s
    assert middleware.process_request(_request(url), spider=object()) is None


def test_challenge_page_retries_via_antibot_provider(clock: _FakeClock) -> None:
    """ResponseStrategy.TRY_ANTIBOT_PROVIDER (docs/REQUIREMENTS.md's own
    Layer 2 spec: "نمط 'صفحة تحدي واضحة' = جرّب antibot provider") -- a
    full HTML page carrying a known challenge marker gets a fresh Request
    back (not a Response), flagged for antibot solving."""
    middleware = CircuitBreakerMiddleware(failure_threshold=5, cooldown_seconds=60.0, clock=clock)
    url = "https://example.com/"
    req = _request(url)
    body = b"<html><body>Please verify you are human to continue.</body></html>"
    result = middleware.process_response(req, _response_with(url, 403, body=body), spider=object())

    assert isinstance(result, Request)
    assert result.url == url
    assert result.meta["antibot_needed"] is True
    assert result.meta["circuit_breaker_antibot_retried"] is True
    assert result.dont_filter is True
    # The original request is untouched -- a *copy* was escalated.
    assert "antibot_needed" not in req.meta


def test_challenge_page_retry_is_not_counted_as_a_circuit_failure(clock: _FakeClock) -> None:
    """The escalation itself is not a circuit-breaker failure -- only a
    retry that comes back classifiable *again* (the guard below) is."""
    middleware = CircuitBreakerMiddleware(failure_threshold=5, cooldown_seconds=60.0, clock=clock)
    url = "https://example.com/"
    body = b"<html><body>Please verify you are human to continue.</body></html>"
    middleware.process_response(
        _request(url), _response_with(url, 403, body=body), spider=object()
    )
    assert middleware._circuits["example.com"].consecutive_failures == 0


def test_challenge_page_retry_guard_stops_a_second_escalation(clock: _FakeClock) -> None:
    """A request already escalated once (circuit_breaker_antibot_retried
    already set) that comes back classifiable again must NOT be
    escalated a second time -- falls through to plain failure counting
    instead, so this can never loop forever."""
    middleware = CircuitBreakerMiddleware(failure_threshold=5, cooldown_seconds=60.0, clock=clock)
    url = "https://example.com/"
    body = b"<html><body>Please verify you are human to continue.</body></html>"
    already_retried = Request(url, meta={"circuit_breaker_antibot_retried": True})

    result = middleware.process_response(
        already_retried, _response_with(url, 403, body=body), spider=object()
    )

    assert isinstance(result, Response)
    assert middleware._circuits["example.com"].consecutive_failures == 1


def test_header_fingerprinted_uses_the_standard_strategy(clock: _FakeClock) -> None:
    """No strategy was given for HEADER_FINGERPRINTED in the Layer 2
    spec -- must behave exactly like a plain failure (counted, opens
    only at failure_threshold), never force-open and never retry."""
    middleware = CircuitBreakerMiddleware(failure_threshold=5, cooldown_seconds=60.0, clock=clock)
    url = "https://example.com/"
    for _ in range(4):
        req = _request(url)
        result = middleware.process_response(
            req, _response_with(url, 403, headers={"X-Antibot-Block": "x"}), spider=object()
        )
        assert isinstance(result, Response)

    assert middleware._circuits["example.com"].consecutive_failures == 4
    assert middleware.process_request(_request(url), spider=object()) is None  # still closed

    req = _request(url)
    middleware.process_response(
        req, _response_with(url, 403, headers={"X-Antibot-Block": "x"}), spider=object()
    )
    with pytest.raises(IgnoreRequest):
        middleware.process_request(_request(url), spider=object())


def test_unrecognized_pattern_uses_the_standard_strategy(clock: _FakeClock) -> None:
    middleware = CircuitBreakerMiddleware(failure_threshold=5, cooldown_seconds=60.0, clock=clock)
    url = "https://example.com/"
    body = b"<html><body>some other, unrelated non-empty page</body></html>"
    for _ in range(5):
        req = _request(url)
        middleware.process_response(req, _response_with(url, 403, body=body), spider=object())

    with pytest.raises(IgnoreRequest):
        middleware.process_request(_request(url), spider=object())


def test_strategy_overrides_change_the_behavior_for_a_named_pattern(clock: _FakeClock) -> None:
    """docs/REQUIREMENTS.md's own Layer 2 spec: "استراتيجية استجابة
    مختلفة (قابلة للتهيئة عبر config)" -- a caller can route
    HEADER_FINGERPRINTED to IMMEDIATE_LONG_BACKOFF instead of the
    module-level default STANDARD, without touching response_classifier.py."""
    middleware = CircuitBreakerMiddleware(
        failure_threshold=5,
        cooldown_seconds=60.0,
        silent_block_cooldown_seconds=300.0,
        strategy_overrides={
            ResponsePattern.HEADER_FINGERPRINTED: ResponseStrategy.IMMEDIATE_LONG_BACKOFF
        },
        clock=clock,
    )
    url = "https://example.com/"
    req = _request(url)
    middleware.process_response(
        req, _response_with(url, 403, headers={"X-Antibot-Block": "x"}), spider=object()
    )

    # A single classified rejection already opened the circuit, well
    # below failure_threshold -- proves the override actually took effect.
    assert middleware._circuits["example.com"].consecutive_failures == 1
    with pytest.raises(IgnoreRequest):
        middleware.process_request(_request(url), spider=object())


def test_silent_block_records_a_failure_registry_entry_on_open(
    clock: _FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded: list[FailureRecord] = []
    monkeypatch.setattr(
        "src.middlewares.circuit_breaker.record_failure",
        lambda record, path=None: recorded.append(record),
    )
    middleware = CircuitBreakerMiddleware(
        failure_threshold=5, cooldown_seconds=60.0, silent_block_cooldown_seconds=300.0, clock=clock
    )
    url = "https://example.com/"
    middleware.process_response(_request(url), _response_with(url, 403), spider=object())

    assert len(recorded) == 1
    record = recorded[0]
    assert record.target == "example.com"
    assert record.failure_category is FailureCategory.ANTIBOT_FINGERPRINT_REJECTION
    assert record.source == "circuit_breaker.opened"
    assert record.raw_signal["reason"] == "classified_silent-block"
    assert record.raw_signal["cooldown_seconds"] == 300.0


def test_challenge_page_retry_records_a_failure_registry_entry(
    clock: _FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded: list[FailureRecord] = []
    monkeypatch.setattr(
        "src.middlewares.circuit_breaker.record_failure",
        lambda record, path=None: recorded.append(record),
    )
    middleware = CircuitBreakerMiddleware(failure_threshold=5, cooldown_seconds=60.0, clock=clock)
    url = "https://example.com/"
    body = b"<html><body>Please verify you are human to continue.</body></html>"
    middleware.process_response(_request(url), _response_with(url, 403, body=body), spider=object())

    assert len(recorded) == 1
    record = recorded[0]
    assert record.target == url
    assert record.failure_category is FailureCategory.ANTIBOT_FINGERPRINT_REJECTION
    assert record.source == "circuit_breaker.retrying_via_antibot_provider"
    assert record.raw_signal["response_pattern"] == "challenge-page"


def test_classified_rejection_under_standard_strategy_records_a_failure_registry_entry(
    clock: _FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded: list[FailureRecord] = []
    monkeypatch.setattr(
        "src.middlewares.circuit_breaker.record_failure",
        lambda record, path=None: recorded.append(record),
    )
    middleware = CircuitBreakerMiddleware(failure_threshold=5, cooldown_seconds=60.0, clock=clock)
    url = "https://example.com/"
    middleware.process_response(
        _request(url), _response_with(url, 403, headers={"X-Antibot-Block": "x"}), spider=object()
    )

    assert len(recorded) == 1
    record = recorded[0]
    assert record.target == url
    assert record.failure_category is FailureCategory.ANTIBOT_FINGERPRINT_REJECTION
    assert record.source == "circuit_breaker.classified_rejection"
    assert record.raw_signal["response_pattern"] == "header-fingerprinted"
    assert record.raw_signal["strategy"] == "standard"


def test_invalid_silent_block_cooldown_raises_value_error() -> None:
    with pytest.raises(ValueError, match="silent_block_cooldown_seconds must be > 0"):
        CircuitBreakerMiddleware(silent_block_cooldown_seconds=0)


def test_from_crawler_reads_the_silent_block_cooldown_setting() -> None:
    class _FakeSettings:
        def getint(self, name: str, default: int) -> int:
            return default

        def getfloat(self, name: str, default: float) -> float:
            return {"TITAN_CIRCUIT_SILENT_BLOCK_COOLDOWN_SECONDS": 120.0}.get(name, default)

        def getbool(self, name: str, default: bool) -> bool:
            return default

        def get(self, name: str, default: object = None) -> object:
            return default

    class _FakeCrawler:
        settings = _FakeSettings()

    built = CircuitBreakerMiddleware.from_crawler(_FakeCrawler())

    assert built.silent_block_cooldown_seconds == 120.0


# docs/REQUIREMENTS.md section 9 entry 30 ("الطبقة 3" -- Strategy
# Engine): CircuitBreakerMiddleware's own real-time hooks into
# StrategyEngine.decide_adjust_backoff / decide_switch_provider.


def test_default_middleware_never_consults_the_engine_for_a_non_opted_in_target(
    clock: _FakeClock,
) -> None:
    """Regression guard: a default CircuitBreakerMiddleware() (its own
    always-constructed, disabled-by-default StrategyEngine) against a
    target that never set strategy_backoff_multiplier must behave
    exactly as it did before this entry -- the engine is never even
    consulted (see _resolve_cooldown's own "opt-in" gate)."""
    middleware = CircuitBreakerMiddleware(cooldown_seconds=60.0, clock=clock)
    url = "https://example.com/"
    for _ in range(5):
        req = _request(url)
        middleware.process_response(req, _response(url, 503), spider=object())

    circuit = middleware._circuits["example.com"]
    assert circuit.cooldown_override is None  # plain cooldown_seconds, unchanged


def test_adjust_backoff_not_consulted_without_a_per_target_multiplier(clock: _FakeClock) -> None:
    """Even with the engine fully enabled and ENACT, a request whose
    target never opted in (no strategy_backoff_multiplier in meta) gets
    zero adjustment -- opt-in is per-target, not global."""
    engine = StrategyEngine(
        StrategyEngineConfig(engine_enabled=True, adjust_backoff_mode=StrategyMode.ENACT)
    )
    middleware = CircuitBreakerMiddleware(
        cooldown_seconds=60.0, clock=clock, strategy_engine=engine
    )
    url = "https://example.com/"
    for _ in range(5):
        req = Request(url)  # no strategy_backoff_multiplier meta at all
        middleware.process_response(req, _response(url, 503), spider=object())

    circuit = middleware._circuits["example.com"]
    assert circuit.cooldown_override is None


def test_adjust_backoff_enacted_scales_the_cooldown_for_an_opted_in_target(
    clock: _FakeClock,
) -> None:
    engine = StrategyEngine(
        StrategyEngineConfig(engine_enabled=True, adjust_backoff_mode=StrategyMode.ENACT)
    )
    middleware = CircuitBreakerMiddleware(
        cooldown_seconds=60.0, clock=clock, strategy_engine=engine
    )
    url = "https://example.com/"
    for _ in range(5):
        req = Request(url, meta={"strategy_backoff_multiplier": 2.0})
        middleware.process_response(req, _response(url, 503), spider=object())

    circuit = middleware._circuits["example.com"]
    assert circuit.cooldown_override == 120.0  # 60.0 * 2.0

    clock.advance(65.0)  # past the plain 60s, still under the adjusted 120s
    with pytest.raises(IgnoreRequest):
        middleware.process_request(_request(url), spider=object())


def test_adjust_backoff_observe_only_does_not_change_the_cooldown(clock: _FakeClock) -> None:
    engine = StrategyEngine(
        StrategyEngineConfig(engine_enabled=True, adjust_backoff_mode=StrategyMode.OBSERVE_ONLY)
    )
    middleware = CircuitBreakerMiddleware(
        cooldown_seconds=60.0, clock=clock, strategy_engine=engine
    )
    url = "https://example.com/"
    for _ in range(5):
        req = Request(url, meta={"strategy_backoff_multiplier": 3.0})
        middleware.process_response(req, _response(url, 503), spider=object())

    circuit = middleware._circuits["example.com"]
    assert circuit.cooldown_override is None  # computed, never applied


def test_adjust_backoff_multiplier_clamped_to_the_global_ceiling(clock: _FakeClock) -> None:
    engine = StrategyEngine(
        StrategyEngineConfig(
            engine_enabled=True,
            adjust_backoff_mode=StrategyMode.ENACT,
            adjust_backoff_max_multiplier=3.0,
        )
    )
    middleware = CircuitBreakerMiddleware(
        cooldown_seconds=60.0, clock=clock, strategy_engine=engine
    )
    url = "https://example.com/"
    for _ in range(5):
        req = Request(url, meta={"strategy_backoff_multiplier": 4.9})
        middleware.process_response(req, _response(url, 503), spider=object())

    circuit = middleware._circuits["example.com"]
    assert circuit.cooldown_override == 180.0  # 60.0 * 3.0 ceiling, not 4.9x


def test_switch_provider_enacted_changes_the_retry_requests_provider(clock: _FakeClock) -> None:
    engine = StrategyEngine(
        StrategyEngineConfig(
            engine_enabled=True,
            switch_provider_mode=StrategyMode.ENACT,
            switch_provider_after_n_challenges=1,
        )
    )
    middleware = CircuitBreakerMiddleware(clock=clock, strategy_engine=engine)
    url = "https://example.com/"
    body = b"<html><body>Please verify you are human to continue.</body></html>"
    req = Request(url, meta={"antibot_provider": "byparr"})

    result = middleware.process_response(req, _response_with(url, 403, body=body), spider=object())

    assert isinstance(result, Request)
    assert result.meta["antibot_provider"] == "camoufox"  # next in PROVIDER_ROTATION after byparr


def test_switch_provider_observe_only_does_not_change_the_provider(clock: _FakeClock) -> None:
    engine = StrategyEngine(
        StrategyEngineConfig(
            engine_enabled=True,
            switch_provider_mode=StrategyMode.OBSERVE_ONLY,
            switch_provider_after_n_challenges=1,
        )
    )
    middleware = CircuitBreakerMiddleware(clock=clock, strategy_engine=engine)
    url = "https://example.com/"
    body = b"<html><body>Please verify you are human to continue.</body></html>"
    req = Request(url, meta={"antibot_provider": "byparr"})

    result = middleware.process_response(req, _response_with(url, 403, body=body), spider=object())

    assert isinstance(result, Request)
    assert result.meta["antibot_provider"] == "byparr"  # unchanged -- observe-only


def test_switch_provider_below_streak_threshold_leaves_the_provider_unchanged(
    clock: _FakeClock,
) -> None:
    engine = StrategyEngine(
        StrategyEngineConfig(
            engine_enabled=True,
            switch_provider_mode=StrategyMode.ENACT,
            switch_provider_after_n_challenges=3,
        )
    )
    middleware = CircuitBreakerMiddleware(clock=clock, strategy_engine=engine)
    url = "https://example.com/"
    body = b"<html><body>Please verify you are human to continue.</body></html>"
    req = Request(url, meta={"antibot_provider": "byparr"})

    result = middleware.process_response(req, _response_with(url, 403, body=body), spider=object())

    assert isinstance(result, Request)
    assert result.meta["antibot_provider"] == "byparr"  # streak == 1, below threshold 3


def test_disabled_engine_never_touches_the_retried_requests_provider(clock: _FakeClock) -> None:
    """The default, always-constructed-but-disabled StrategyEngine
    (CircuitBreakerMiddleware()'s own default) must leave
    TRY_ANTIBOT_PROVIDER's own Layer 2 behavior completely untouched."""
    middleware = CircuitBreakerMiddleware(clock=clock)
    url = "https://example.com/"
    body = b"<html><body>Please verify you are human to continue.</body></html>"
    req = Request(url, meta={"antibot_provider": "byparr"})

    result = middleware.process_response(req, _response_with(url, 403, body=body), spider=object())

    assert isinstance(result, Request)
    assert result.meta["antibot_provider"] == "byparr"
