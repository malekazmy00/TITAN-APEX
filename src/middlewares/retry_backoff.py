"""Downloader middleware: retry with exponential backoff.

Retries a request on a retryable HTTP status code or a transient network
exception, delaying each successive attempt by an exponentially growing
interval (capped at ``max_delay``). Once ``max_attempts`` is exhausted the
request is dropped via :class:`scrapy.exceptions.IgnoreRequest`, with the
reason logged (never a silent drop).
"""

from __future__ import annotations

from collections.abc import Callable
from logging import Logger
from typing import Any

from scrapy.exceptions import IgnoreRequest
from scrapy.http import Request, Response
from twisted.internet import reactor
from twisted.internet.defer import Deferred
from twisted.internet.error import (
    ConnectionRefusedError as TwistedConnectionRefusedError,
)
from twisted.internet.error import (
    DNSLookupError,
    TCPTimedOutError,
)
from twisted.internet.error import (
    TimeoutError as TwistedTimeoutError,
)

from src.logging_config import get_logger

RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    TwistedTimeoutError,
    TwistedConnectionRefusedError,
    DNSLookupError,
    TCPTimedOutError,
)

ScheduleCall = Callable[[float, Callable[[], None]], None]


def compute_delay(attempt: int, base_delay: float, max_delay: float = 60.0) -> float:
    """Exponential backoff delay (seconds) for the given 1-indexed ``attempt``.

    Raises:
        ValueError: if ``attempt`` is less than 1.
    """
    if attempt < 1:
        raise ValueError(f"attempt must be >= 1, got {attempt}")
    delay = base_delay * (2.0 ** (attempt - 1))
    return min(delay, max_delay)


def _default_schedule_call(delay: float, callback: Callable[[], None]) -> None:
    # mypy misresolves IReactorTime.callLater's signature through the
    # zope.interface layer (verified against the real runtime signature,
    # which matches this call exactly: delay, callable, *args, **kw).
    reactor.callLater(delay, callback)  # type: ignore[arg-type]


class RetryBackoffMiddleware:
    """Scrapy downloader middleware implementing retry + exponential backoff."""

    def __init__(
        self,
        max_attempts: int = 5,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        logger: Logger | None = None,
        schedule_call: ScheduleCall | None = None,
    ) -> None:
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.logger = logger or get_logger(__name__)
        self._schedule_call = schedule_call or _default_schedule_call

    @classmethod
    def from_crawler(cls, crawler: Any) -> RetryBackoffMiddleware:
        settings = crawler.settings
        return cls(
            max_attempts=settings.getint("TITAN_RETRY_MAX_ATTEMPTS", 5),
            base_delay=settings.getfloat("TITAN_RETRY_BASE_DELAY", 1.0),
            max_delay=settings.getfloat("TITAN_RETRY_MAX_DELAY", 60.0),
        )

    def process_response(
        self, request: Request, response: Response, spider: Any
    ) -> Response | Deferred[Request]:
        if response.status not in RETRYABLE_STATUSES:
            return response

        attempt = self._next_attempt(request)
        reason = f"http_{response.status}"
        if attempt > self.max_attempts:
            self._log_giving_up(request, reason, attempt)
            return response
        return self._schedule_retry(request, attempt, reason)

    def process_exception(
        self, request: Request, exception: Exception, spider: Any
    ) -> Deferred[Request] | None:
        if not isinstance(exception, RETRYABLE_EXCEPTIONS):
            return None

        attempt = self._next_attempt(request)
        reason = type(exception).__name__
        if attempt > self.max_attempts:
            self._log_giving_up(request, reason, attempt)
            raise IgnoreRequest(
                f"retry_backoff: giving up on {request.url} after {attempt - 1} attempts "
                f"({reason})"
            )
        return self._schedule_retry(request, attempt, reason)

    @staticmethod
    def _next_attempt(request: Request) -> int:
        return int(request.meta.get("retry_backoff_attempt", 0)) + 1

    def _log_giving_up(self, request: Request, reason: str, attempt: int) -> None:
        self.logger.error(
            "retry_backoff.giving_up",
            extra={"url": request.url, "reason": reason, "attempts": attempt - 1},
        )

    def _schedule_retry(self, request: Request, attempt: int, reason: str) -> Deferred[Request]:
        delay = compute_delay(attempt, self.base_delay, self.max_delay)
        new_request = request.copy()
        new_request.meta["retry_backoff_attempt"] = attempt
        new_request.dont_filter = True

        self.logger.warning(
            "retry_backoff.scheduled",
            extra={"url": request.url, "reason": reason, "attempt": attempt, "delay": delay},
        )

        deferred: Deferred[Request] = Deferred()
        self._schedule_call(delay, lambda: deferred.callback(new_request))
        return deferred
