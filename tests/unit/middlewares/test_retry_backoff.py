"""Unit tests for src/middlewares/retry_backoff.py.

Real network delays are never awaited here: ``schedule_call`` is injected
and invoked synchronously so the tests stay fast and deterministic.
"""

from __future__ import annotations

import pytest
from scrapy.exceptions import IgnoreRequest
from scrapy.http import Request, Response, TextResponse
from twisted.internet.defer import Deferred
from twisted.internet.error import TimeoutError as TwistedTimeoutError

from src.middlewares.retry_backoff import RetryBackoffMiddleware, compute_delay


def _immediate_schedule_call(delay: float, callback: object) -> None:
    callback()  # type: ignore[operator]


@pytest.fixture
def middleware() -> RetryBackoffMiddleware:
    return RetryBackoffMiddleware(
        max_attempts=2,
        base_delay=0.01,
        schedule_call=_immediate_schedule_call,
    )


def test_compute_delay_grows_exponentially() -> None:
    assert compute_delay(1, base_delay=1.0) == 1.0
    assert compute_delay(2, base_delay=1.0) == 2.0
    assert compute_delay(3, base_delay=1.0) == 4.0


def test_compute_delay_is_capped_at_max_delay() -> None:
    assert compute_delay(10, base_delay=1.0, max_delay=5.0) == 5.0


def test_compute_delay_rejects_non_positive_attempt() -> None:
    with pytest.raises(ValueError, match="attempt must be >= 1"):
        compute_delay(0, base_delay=1.0)


def test_process_response_passes_through_success(middleware: RetryBackoffMiddleware) -> None:
    """Happy path: a 200 response is returned unchanged, no retry scheduled."""
    request = Request("https://example.com/")
    response = TextResponse(url="https://example.com/", status=200, request=request)

    result = middleware.process_response(request, response, spider=object())

    assert result is response


def test_process_response_schedules_a_retry_on_retryable_status(
    middleware: RetryBackoffMiddleware,
) -> None:
    """Failure case 1: a 503 is retried with an incremented attempt count."""
    request = Request("https://example.com/")
    response = Response(url="https://example.com/", status=503, request=request)

    result = middleware.process_response(request, response, spider=object())

    assert isinstance(result, Deferred)
    retried_request = result.result
    assert isinstance(retried_request, Request)
    assert retried_request.meta["retry_backoff_attempt"] == 1
    assert retried_request.dont_filter is True


def test_process_response_gives_up_after_max_attempts(
    middleware: RetryBackoffMiddleware,
) -> None:
    """Failure case 2: once max_attempts is exhausted, the last response is returned."""
    request = Request("https://example.com/", meta={"retry_backoff_attempt": 2})
    response = Response(url="https://example.com/", status=503, request=request)

    result = middleware.process_response(request, response, spider=object())

    assert result is response


def test_process_exception_ignores_non_retryable_errors(
    middleware: RetryBackoffMiddleware,
) -> None:
    request = Request("https://example.com/")

    result = middleware.process_exception(
        request, ValueError("not a network error"), spider=object()
    )

    assert result is None


def test_process_exception_schedules_a_retry_on_retryable_exception(
    middleware: RetryBackoffMiddleware,
) -> None:
    request = Request("https://example.com/")

    result = middleware.process_exception(
        request, TwistedTimeoutError("timed out"), spider=object()
    )

    assert isinstance(result, Deferred)
    retried_request = result.result
    assert retried_request.meta["retry_backoff_attempt"] == 1


def test_process_exception_raises_ignore_request_after_max_attempts(
    middleware: RetryBackoffMiddleware,
) -> None:
    """When there is no response to fall back on, the request is dropped loudly."""
    request = Request("https://example.com/", meta={"retry_backoff_attempt": 2})

    with pytest.raises(IgnoreRequest):
        middleware.process_exception(request, TwistedTimeoutError("timed out"), spider=object())


def test_from_crawler_reads_settings() -> None:
    class _FakeSettings:
        def getint(self, name: str, default: int) -> int:
            return {"TITAN_RETRY_MAX_ATTEMPTS": 7}.get(name, default)

        def getfloat(self, name: str, default: float) -> float:
            return {"TITAN_RETRY_BASE_DELAY": 2.0, "TITAN_RETRY_MAX_DELAY": 30.0}.get(
                name, default
            )

    class _FakeCrawler:
        settings = _FakeSettings()

    built = RetryBackoffMiddleware.from_crawler(_FakeCrawler())

    assert built.max_attempts == 7
    assert built.base_delay == 2.0
    assert built.max_delay == 30.0
