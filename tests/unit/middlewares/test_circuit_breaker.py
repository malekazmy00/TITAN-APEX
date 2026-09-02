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
