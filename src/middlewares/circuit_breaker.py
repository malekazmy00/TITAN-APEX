"""Downloader middleware: per-domain circuit breaker.

After ``failure_threshold`` consecutive failures against a domain, the
circuit opens: every further request to that domain is rejected
immediately (no network call, logged clearly) for ``cooldown_seconds``.
Once the cooldown elapses, one trial request is let through (half-open) —
success closes the circuit, failure reopens it for another full cooldown.

Defaults match docs/REQUIREMENTS.md: 5 consecutive failures, 60s cooldown.

docs/REQUIREMENTS.md section 9, "الطبقة 2" (Protection Classifier): a
plain 5xx or a request-level exception (``FAILURE_STATUSES``, the
original mechanism above) is only ever "the network/server is having
trouble" — there's nothing more to learn from the response itself. An
antibot-style rejection (``CLASSIFIABLE_STATUSES`` — 401/403/407/429) is
different: the *shape* of the rejection (empty body vs. a named
vendor header vs. a readable challenge page — see
``src.response_classifier``) carries real, actionable signal a bare
status code throws away. ``_handle_classifiable_response`` below is that
second, narrower path — a genuine extension of this middleware, not a
separate one, since both ultimately govern the same per-domain circuit
state.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from enum import Enum
from logging import Logger
from typing import Any
from urllib.parse import urlparse

from scrapy.exceptions import IgnoreRequest
from scrapy.http import Request, Response

from src.alerting import AlertDispatcher, AlertEvent, dispatcher_from_settings
from src.diagnostics.failure_registry import record_failure
from src.diagnostics.failure_taxonomy import FailureCategory, FailureRecord
from src.logging_config import get_logger
from src.response_classifier import (
    ResponsePattern,
    ResponseStrategy,
    classify_response,
    strategy_for,
)

FAILURE_STATUSES = frozenset({500, 502, 503, 504})

# docs/REQUIREMENTS.md section 9, "الطبقة 2": the user's own examples,
# "403/429/إلخ" -- disjoint from FAILURE_STATUSES on purpose (a 5xx is
# never a target's own antibot fingerprinting decision; these four are
# the standard HTTP statuses a WAF/antibot layer actually returns).
CLASSIFIABLE_STATUSES = frozenset({401, 403, 407, 429})

DEFAULT_FAILURE_THRESHOLD = 5
DEFAULT_COOLDOWN_SECONDS = 60.0
# ResponseStrategy.IMMEDIATE_LONG_BACKOFF's own cooldown -- deliberately
# longer than DEFAULT_COOLDOWN_SECONDS (docs/REQUIREMENTS.md's own Layer
# 2 spec: "نمط 'فاضي بلا علامة' = backoff طويل فورًا"). 5x the plain
# default is a starting point, not a tuned value -- overridable via
# TITAN_CIRCUIT_SILENT_BLOCK_COOLDOWN_SECONDS, same as every other
# circuit-breaker constant here.
DEFAULT_SILENT_BLOCK_COOLDOWN_SECONDS = 300.0


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class _DomainCircuit:
    __slots__ = ("state", "consecutive_failures", "opened_at", "cooldown_override")

    def __init__(self) -> None:
        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0
        # Only meaningful while state is OPEN; 0.0 otherwise.
        self.opened_at: float = 0.0
        # None means "use the middleware's own cooldown_seconds" (the
        # normal case). Set to a specific value only when this circuit
        # was forced open by ResponseStrategy.IMMEDIATE_LONG_BACKOFF,
        # which needs a longer cooldown than a plain threshold-triggered
        # open -- reset to None on every close (_record_success) so a
        # later, ordinary open doesn't inherit a stale extended cooldown.
        self.cooldown_override: float | None = None


class CircuitBreakerMiddleware:
    """Scrapy downloader middleware implementing a per-domain circuit breaker."""

    def __init__(
        self,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        silent_block_cooldown_seconds: float = DEFAULT_SILENT_BLOCK_COOLDOWN_SECONDS,
        strategy_overrides: Mapping[ResponsePattern, ResponseStrategy] | None = None,
        clock: Callable[[], float] | None = None,
        logger: Logger | None = None,
        alert_dispatcher: AlertDispatcher | None = None,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError(f"failure_threshold must be >= 1, got {failure_threshold}")
        if cooldown_seconds <= 0:
            raise ValueError(f"cooldown_seconds must be > 0, got {cooldown_seconds}")
        if silent_block_cooldown_seconds <= 0:
            raise ValueError(
                f"silent_block_cooldown_seconds must be > 0, got {silent_block_cooldown_seconds}"
            )
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.silent_block_cooldown_seconds = silent_block_cooldown_seconds
        # docs/REQUIREMENTS.md's own Layer 2 spec: "استراتيجية استجابة
        # مختلفة (قابلة للتهيئة عبر config)" -- a target/domain that
        # needs a non-default strategy for a given ResponsePattern (e.g.
        # a target known to have no antibot provider configured at all,
        # where TRY_ANTIBOT_PROVIDER would just waste a retry) can
        # override it here, without touching response_classifier.py's
        # own DEFAULT_STRATEGY_FOR_PATTERN.
        self.strategy_overrides = strategy_overrides
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
            silent_block_cooldown_seconds=settings.getfloat(
                "TITAN_CIRCUIT_SILENT_BLOCK_COOLDOWN_SECONDS",
                DEFAULT_SILENT_BLOCK_COOLDOWN_SECONDS,
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
            cooldown = (
                circuit.cooldown_override
                if circuit.cooldown_override is not None
                else self.cooldown_seconds
            )
            elapsed = self._clock() - circuit.opened_at
            if elapsed < cooldown:
                self.logger.error(
                    "circuit_breaker.blocked",
                    extra={
                        "domain": domain,
                        "elapsed_seconds": elapsed,
                        "cooldown_seconds": cooldown,
                    },
                )
                raise IgnoreRequest(
                    f"circuit_breaker: {domain} is open "
                    f"({elapsed:.1f}s of {cooldown}s cooldown elapsed)"
                )
            circuit.state = CircuitState.HALF_OPEN
            self.logger.warning("circuit_breaker.half_open", extra={"domain": domain})
        return None

    def process_response(
        self, request: Request, response: Response, spider: Any
    ) -> Response | Request:
        domain = self._domain(request)
        circuit = self._circuit_for(domain)
        if response.status in FAILURE_STATUSES:
            self._record_failure(domain, circuit, reason=f"http_{response.status}")
        elif response.status in CLASSIFIABLE_STATUSES:
            return self._handle_classifiable_response(request, response, domain, circuit)
        else:
            self._record_success(domain, circuit)
        return response

    def process_exception(self, request: Request, exception: Exception, spider: Any) -> None:
        domain = self._domain(request)
        circuit = self._circuit_for(domain)
        self._record_failure(domain, circuit, reason=type(exception).__name__)
        return None

    def _handle_classifiable_response(
        self, request: Request, response: Response, domain: str, circuit: _DomainCircuit
    ) -> Response | Request:
        """docs/REQUIREMENTS.md section 9, "الطبقة 2": ``response`` is
        already known to carry a ``CLASSIFIABLE_STATUSES`` status --
        classify *how* it rejected the request and act per
        ``ResponseStrategy`` instead of treating every antibot-style
        rejection identically the way the plain ``FAILURE_STATUSES``
        path always has.
        """
        # to_unicode_dict() returns scrapy's own CaseInsensitiveDict,
        # typed by scrapy's stubs as allowing str-or-bytes keys/values
        # (Headers can hold either in general) even though
        # to_unicode_dict()'s own docstring guarantees str/str here --
        # the explicit str() calls satisfy classify_response's plain
        # Mapping[str, str] contract under mypy --strict; harmless
        # either way since classify_response already lowercases every
        # key itself (its own docstring covers why it never assumes
        # case-insensitivity from the caller).
        normalized_headers = {
            str(name): str(value) for name, value in response.headers.to_unicode_dict().items()
        }
        pattern = classify_response(normalized_headers, response.body)
        strategy = strategy_for(pattern, overrides=self.strategy_overrides)
        self.logger.warning(
            "circuit_breaker.classified_rejection",
            extra={
                "domain": domain,
                "url": request.url,
                "status": response.status,
                "pattern": pattern.value,
                "strategy": strategy.value,
            },
        )

        if strategy is ResponseStrategy.IMMEDIATE_LONG_BACKOFF:
            # Coincides exactly with the circuit opening -- a single
            # record via _open_circuit below, not a separate
            # "classified_rejection" one too (that would just double-
            # record the same instant, the exact double-counting entry
            # 28's own byparr_middleware decision already reasoned
            # about avoiding).
            circuit.consecutive_failures += 1
            self._open_circuit(
                domain,
                circuit,
                reason=f"classified_{pattern.value}",
                cooldown_seconds=self.silent_block_cooldown_seconds,
                failure_category=FailureCategory.ANTIBOT_FINGERPRINT_REJECTION,
            )
            return response

        if strategy is ResponseStrategy.TRY_ANTIBOT_PROVIDER:
            # Guards against a loop: a request already escalated once by
            # this exact mechanism that comes back classifiable *again*
            # falls through to the plain failure-counting path instead
            # of retrying forever (this project's own mock-target
            # /reject-pattern route, for one, always returns the same
            # static challenge page -- a real antibot provider could
            # never actually "solve" it, so an unguarded retry here
            # would loop until Scrapy's own DEPTH_LIMIT/other request-
            # level ceiling stopped it, not a clean, intentional stop).
            if request.meta.get("circuit_breaker_antibot_retried"):
                self._record_failure(
                    domain,
                    circuit,
                    reason=f"classified_{pattern.value}_antibot_retry_exhausted",
                    failure_category=FailureCategory.ANTIBOT_FINGERPRINT_REJECTION,
                )
                return response
            new_request = request.copy()
            new_request.meta["antibot_needed"] = True
            new_request.meta["circuit_breaker_antibot_retried"] = True
            new_request.dont_filter = True
            self.logger.warning(
                "circuit_breaker.retrying_via_antibot_provider",
                extra={"domain": domain, "url": request.url, "pattern": pattern.value},
            )
            record_failure(
                FailureRecord(
                    timestamp=datetime.now(tz=UTC),
                    target=request.url,
                    failure_category=FailureCategory.ANTIBOT_FINGERPRINT_REJECTION,
                    raw_signal={
                        "status": response.status,
                        "response_pattern": pattern.value,
                        "strategy": strategy.value,
                    },
                    source="circuit_breaker.retrying_via_antibot_provider",
                )
            )
            return new_request

        # ResponseStrategy.STANDARD -- HEADER_FINGERPRINTED/UNRECOGNIZED
        # by default, or any pattern a strategy_overrides mapping routed
        # here deliberately. Recorded per classified event (unlike the
        # plain FAILURE_STATUSES path, which only ever records the
        # "opened" moment) because the pattern itself -- not just a
        # running count -- is the useful signal Layer 2 adds; matches
        # the same per-event granularity byparr_provider.py/
        # camoufox_provider.py already record at for their own,
        # more-specific antibot rejections.
        record_failure(
            FailureRecord(
                timestamp=datetime.now(tz=UTC),
                target=request.url,
                failure_category=FailureCategory.ANTIBOT_FINGERPRINT_REJECTION,
                raw_signal={
                    "status": response.status,
                    "response_pattern": pattern.value,
                    "strategy": strategy.value,
                },
                source="circuit_breaker.classified_rejection",
            )
        )
        self._record_failure(
            domain,
            circuit,
            reason=f"classified_{pattern.value}",
            failure_category=FailureCategory.ANTIBOT_FINGERPRINT_REJECTION,
        )
        return response

    def _record_failure(
        self,
        domain: str,
        circuit: _DomainCircuit,
        reason: str,
        failure_category: FailureCategory = FailureCategory.NETWORK_INFRA_TRANSIENT,
    ) -> None:
        circuit.consecutive_failures += 1
        if circuit.state is CircuitState.HALF_OPEN or (
            circuit.consecutive_failures >= self.failure_threshold
        ):
            self._open_circuit(
                domain,
                circuit,
                reason=reason,
                cooldown_seconds=self.cooldown_seconds,
                failure_category=failure_category,
            )

    def _open_circuit(
        self,
        domain: str,
        circuit: _DomainCircuit,
        reason: str,
        cooldown_seconds: float,
        failure_category: FailureCategory,
    ) -> None:
        circuit.state = CircuitState.OPEN
        circuit.opened_at = self._clock()
        # None (== "use the middleware's own cooldown_seconds") for the
        # plain, threshold-triggered open path -- only a genuinely
        # different (longer) cooldown is worth storing per-circuit; see
        # _DomainCircuit.cooldown_override's own docstring.
        circuit.cooldown_override = (
            cooldown_seconds if cooldown_seconds != self.cooldown_seconds else None
        )
        self.logger.error(
            "circuit_breaker.opened",
            extra={
                "domain": domain,
                "reason": reason,
                "consecutive_failures": circuit.consecutive_failures,
                "cooldown_seconds": cooldown_seconds,
            },
        )
        self._alert_dispatcher.send(
            AlertEvent(
                source="circuit_breaker",
                domain=domain,
                reason=reason,
                consecutive_failures=circuit.consecutive_failures,
                cooldown_seconds=cooldown_seconds,
                occurred_at=datetime.now(tz=UTC),
            )
        )
        # Unified failure taxonomy (docs/REQUIREMENTS.md section 9
        # entry 28) -- the "open" event specifically, not every
        # individual contributing failure below threshold: this is
        # the moment the user's own named diagnostic ("Circuit
        # Breaker open/close events") actually fires, and
        # raw_signal already carries consecutive_failures for
        # anyone who needs the run-up. failure_category now reflects
        # *why* this circuit actually opened (section 9's "الطبقة 2"
        # addition) instead of always assuming network-infra-transient:
        # a circuit forced open by a classified antibot rejection (or
        # one that crossed failure_threshold via a run of them) is
        # correctly antibot-fingerprint-rejection, not a guess.
        record_failure(
            FailureRecord(
                timestamp=datetime.now(tz=UTC),
                target=domain,
                failure_category=failure_category,
                raw_signal={
                    "reason": reason,
                    "consecutive_failures": circuit.consecutive_failures,
                    "cooldown_seconds": cooldown_seconds,
                },
                source="circuit_breaker.opened",
            )
        )

    def _record_success(self, domain: str, circuit: _DomainCircuit) -> None:
        if circuit.state is not CircuitState.CLOSED:
            self.logger.warning("circuit_breaker.closed", extra={"domain": domain})
        circuit.state = CircuitState.CLOSED
        circuit.consecutive_failures = 0
        circuit.opened_at = 0.0
        circuit.cooldown_override = None
