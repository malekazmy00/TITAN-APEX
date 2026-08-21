"""Downloader middleware: per-domain circuit breaker.

After ``failure_threshold`` consecutive failures against a domain, the
circuit opens: every further request to that domain is rejected
immediately (no network call, logged clearly) for ``cooldown_seconds``.
Once the cooldown elapses, one trial request is let through (half-open) —
success closes the circuit, failure reopens it for another full cooldown.

Defaults match docs/REQUIREMENTS.md: 5 consecutive failures, 60s cooldown.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from enum import Enum
from logging import Logger
from typing import Any
from urllib.parse import urlparse

from scrapy.exceptions import IgnoreRequest
from scrapy.http import Request, Response

from src.alerting import AlertDispatcher, AlertEvent, dispatcher_from_settings
from src.logging_config import get_logger

FAILURE_STATUSES = frozenset({500, 502, 503, 504})

DEFAULT_FAILURE_THRESHOLD = 5
DEFAULT_COOLDOWN_SECONDS = 60.0


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class _DomainCircuit:
    __slots__ = ("state", "consecutive_failures", "opened_at")

    def __init__(self) -> None:
        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0
        # Only meaningful while state is OPEN; 0.0 otherwise.
        self.opened_at: float = 0.0


class CircuitBreakerMiddleware:
    """Scrapy downloader middleware implementing a per-domain circuit breaker."""

    def __init__(
        self,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        clock: Callable[[], float] | None = None,
        logger: Logger | None = None,
        alert_dispatcher: AlertDispatcher | None = None,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError(f"failure_threshold must be >= 1, got {failure_threshold}")
        if cooldown_seconds <= 0:
            raise ValueError(f"cooldown_seconds must be > 0, got {cooldown_seconds}")
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._clock = clock or time.monotonic
        self.logger = logger or get_logger(__name__)
        self._alert_dispatcher = alert_dispatcher or AlertDispatcher()
        self._circuits: dict[str, _DomainCircuit] = {}

    @classmethod
    def from_crawler(cls, crawler: Any) -> CircuitBreakerMiddleware:
        settings = crawler.settings
        return cls(
            failure_threshold=settings.getint(
                "TITAN_CIRCUIT_FAILURE_THRESHOLD", DEFAULT_FAILURE_THRESHOLD
            ),
            cooldown_seconds=settings.getfloat(
                "TITAN_CIRCUIT_COOLDOWN_SECONDS", DEFAULT_COOLDOWN_SECONDS
            ),
            alert_dispatcher=dispatcher_from_settings(settings),
        )

    @staticmethod
    def _domain(request: Request) -> str:
        return urlparse(request.url).netloc

    def _circuit_for(self, domain: str) -> _DomainCircuit:
        if domain not in self._circuits:
            self._circuits[domain] = _DomainCircuit()
        return self._circuits[domain]

    def process_request(self, request: Request, spider: Any) -> None:
        domain = self._domain(request)
        circuit = self._circuit_for(domain)

        if circuit.state is CircuitState.OPEN:
            elapsed = self._clock() - circuit.opened_at
            if elapsed < self.cooldown_seconds:
                self.logger.error(
                    "circuit_breaker.blocked",
                    extra={
                        "domain": domain,
                        "elapsed_seconds": elapsed,
                        "cooldown_seconds": self.cooldown_seconds,
                    },
                )
                raise IgnoreRequest(
                    f"circuit_breaker: {domain} is open "
                    f"({elapsed:.1f}s of {self.cooldown_seconds}s cooldown elapsed)"
                )
            circuit.state = CircuitState.HALF_OPEN
            self.logger.warning("circuit_breaker.half_open", extra={"domain": domain})
        return None

    def process_response(self, request: Request, response: Response, spider: Any) -> Response:
        domain = self._domain(request)
        circuit = self._circuit_for(domain)
        if response.status in FAILURE_STATUSES:
            self._record_failure(domain, circuit, reason=f"http_{response.status}")
        else:
            self._record_success(domain, circuit)
        return response

    def process_exception(self, request: Request, exception: Exception, spider: Any) -> None:
        domain = self._domain(request)
        circuit = self._circuit_for(domain)
        self._record_failure(domain, circuit, reason=type(exception).__name__)
        return None

    def _record_failure(self, domain: str, circuit: _DomainCircuit, reason: str) -> None:
        circuit.consecutive_failures += 1
        if circuit.state is CircuitState.HALF_OPEN or (
            circuit.consecutive_failures >= self.failure_threshold
        ):
            circuit.state = CircuitState.OPEN
            circuit.opened_at = self._clock()
            self.logger.error(
                "circuit_breaker.opened",
                extra={
                    "domain": domain,
                    "reason": reason,
                    "consecutive_failures": circuit.consecutive_failures,
                    "cooldown_seconds": self.cooldown_seconds,
                },
            )
            self._alert_dispatcher.send(
                AlertEvent(
                    source="circuit_breaker",
                    domain=domain,
                    reason=reason,
                    consecutive_failures=circuit.consecutive_failures,
                    cooldown_seconds=self.cooldown_seconds,
                    occurred_at=datetime.now(tz=UTC),
                )
            )

    def _record_success(self, domain: str, circuit: _DomainCircuit) -> None:
        if circuit.state is not CircuitState.CLOSED:
            self.logger.warning("circuit_breaker.closed", extra={"domain": domain})
        circuit.state = CircuitState.CLOSED
        circuit.consecutive_failures = 0
        circuit.opened_at = 0.0
