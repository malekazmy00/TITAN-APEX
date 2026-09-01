"""Downloader middleware: multi-level, pattern-aware rate limiting.

docs/REQUIREMENTS.md section 9 entry 22 (Phase 2, بند 7 بطلب المستخدم
صراحة). Independent from ``retry_backoff.py``/``circuit_breaker.py`` on
purpose -- this middleware only ever looks at *outgoing request timing*
(``process_request``), never a response's status or an exception, so it
has no ``process_response``/``process_exception`` at all. It never
touches Camoufox/Patchright/session/navigation code -- it is a plain
Scrapy downloader middleware operating on ``Request`` objects and
``request.meta`` only, wired in the same way
``CircuitBreakerMiddleware``/``RetryBackoffMiddleware`` already are.

**Three real capabilities, per the explicit request:**

1. **Multi-level, not just per-target.** The existing per-target
   ``rate_limit``/``max_concurrency`` (``spider_config.py``, Scrapy's own
   ``DOWNLOAD_DELAY``/``CONCURRENT_REQUESTS_PER_DOMAIN``) is a single,
   fixed-delay knob per target domain -- this adds independently
   configurable *levels*, each keyed by its own :data:`RateLimitLevel.key_fn`:
   ``target`` (the domain, always available -- same ``urlparse(...).netloc``
   :class:`~src.middlewares.circuit_breaker.CircuitBreakerMiddleware`
   already uses), ``account`` (``request.meta["account_id"]``),
   ``ip`` (``request.meta["egress_ip"]``), and ``asn_subnet``
   (``request.meta["egress_asn"]``). This project has no account concept
   and Phase 6 (proxies, egress IP/ASN diversity) is still deferred
   (section 2's own roadmap) -- so today, every request's ``account``/
   ``ip``/``asn_subnet`` key is ``None`` and those three levels are a
   harmless no-op for every existing spider/config. That is deliberate,
   not a gap to fix later: :data:`RateLimitLevel.key_fn` returning
   ``None`` means "not applicable to this request", not "violation" --
   a level with no meta support yet simply never counts anything,
   forward-compatible with a future login/proxy layer setting that meta
   without a single change to this module.

2. **"Smart" -- request *pattern*, not just a raw counter.** A level's
   own count-based window (``max_requests`` per ``window_seconds``,
   the "did we send too many" check) is only half of what triggers a
   violation. The other half, :func:`is_pattern_too_regular`, looks at
   the *shape* of a scope's own recent inter-request intervals via their
   coefficient of variation (population stddev / mean) -- naturally
   jittered traffic (human timing, or Scrapy's own randomized delay) has
   a meaningfully non-zero CV; a mechanically fixed cadence (the exact
   giveaway a bot-detection system looks for, same theme as this
   project's own fpscanner/JA4/mouse-movement work, just applied to our
   *own* outgoing request timing instead of browser fingerprint signals)
   has a CV near zero. Below ``regularity_cv_threshold`` (with at least
   ``regularity_min_intervals`` samples -- too few intervals make CV
   meaningless/noisy) counts as a second, independent violation reason
   (``"pattern_detected"``), logged and escalated exactly like
   ``"count_exceeded"``.

3. **Escalating backoff on repeated violations.** A single violation
   (either reason) only logs a ``WARNING`` and lets the request through
   -- ``violation_threshold`` consecutive violations (default 3) are
   required before a scope is actually blocked, the same
   "don't overreact to one blip" shape
   :class:`~src.middlewares.circuit_breaker.CircuitBreakerMiddleware`
   already has for HTTP failures. Once blocked, the cooldown itself
   grows exponentially with each further block
   (:func:`compute_escalated_backoff_seconds`, the same doubling shape
   ``retry_backoff.compute_delay`` already has -- duplicated rather than
   imported: different semantics, a *scope's own escalating cooldown*
   here vs. a *per-request retry delay* there, the same
   "~10 duplicated lines is cheaper than a cross-middleware import for a
   different concept" tradeoff ``_scroll.py``'s own module docstring
   already documents for an analogous case), capped at
   ``backoff_max_seconds``. A sufficiently long clean streak
   (``violation_reset_seconds`` with zero violations) resets a scope's
   violation count back to zero -- mirrors
   ``CircuitBreakerMiddleware._record_success`` resetting
   ``consecutive_failures``.

**Correctness detail worth stating explicitly:** a request that ends up
*dropped* (any level escalates to a hard block this round) must never
have its timestamp recorded against any level's own window -- it never
actually went out. :meth:`RateLimiterMiddleware.process_request` is
therefore a strict three-phase evaluation (existing cooldowns -> read-only
violation detection across every applicable level -> commit), never
mutating a level's ``request_times``/``violation_count`` until the whole
request's fate (through vs. dropped) is already decided.

Default per-level thresholds below are a reasonable, clearly-labeled
starting point, not numbers measured from real traffic (this project has
no historical request-volume data to calibrate against yet) -- every one
is independently configurable via ``TITAN_RATE_LIMIT_*`` settings
(``.env.example``), the same "no hardcoding, everything through config"
principle every other middleware here already follows.
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from logging import Logger
from typing import Any
from urllib.parse import urlparse

from scrapy.exceptions import IgnoreRequest
from scrapy.http import Request

from src.alerting import AlertDispatcher, AlertEvent, dispatcher_from_settings
from src.logging_config import get_logger

DEFAULT_TARGET_MAX_REQUESTS = 30
DEFAULT_TARGET_WINDOW_SECONDS = 60.0
DEFAULT_ACCOUNT_MAX_REQUESTS = 30
DEFAULT_ACCOUNT_WINDOW_SECONDS = 60.0
DEFAULT_IP_MAX_REQUESTS = 60
DEFAULT_IP_WINDOW_SECONDS = 60.0
DEFAULT_ASN_MAX_REQUESTS = 120
DEFAULT_ASN_WINDOW_SECONDS = 60.0

DEFAULT_REGULARITY_MIN_INTERVALS = 5
DEFAULT_REGULARITY_CV_THRESHOLD = 0.15

DEFAULT_VIOLATION_THRESHOLD = 3
DEFAULT_BACKOFF_BASE_SECONDS = 30.0
DEFAULT_BACKOFF_MAX_SECONDS = 1800.0
DEFAULT_VIOLATION_RESET_SECONDS = 300.0


def coefficient_of_variation(intervals: Sequence[float]) -> float | None:
    """Population stddev / mean of ``intervals`` -- ``None`` (undefined,
    not zero) when there are fewer than 2 samples or the mean is not
    strictly positive (a zero/negative-mean interval sequence can only
    happen from malformed input; never treated as "perfectly regular").
    """
    if len(intervals) < 2:
        return None
    mean = sum(intervals) / len(intervals)
    if mean <= 0:
        return None
    variance = sum((value - mean) ** 2 for value in intervals) / len(intervals)
    return math.sqrt(variance) / mean


def is_pattern_too_regular(
    intervals: Sequence[float], min_intervals: int, cv_threshold: float
) -> bool:
    """``True`` when ``intervals`` has at least ``min_intervals`` samples
    and their coefficient of variation is below ``cv_threshold`` -- a
    mechanically fixed request cadence, the real signature this exists to
    catch (this module's own docstring, point 2).

    Raises:
        ValueError: if ``min_intervals`` is below 2 (a CV needs at least
            2 samples to be defined at all) or ``cv_threshold`` is not
            strictly positive (both meaningless configurations).
    """
    if min_intervals < 2:
        raise ValueError(f"min_intervals must be >= 2, got {min_intervals}")
    if cv_threshold <= 0:
        raise ValueError(f"cv_threshold must be > 0, got {cv_threshold}")
    if len(intervals) < min_intervals:
        return False
    cv = coefficient_of_variation(intervals)
    if cv is None:
        return False
    return cv < cv_threshold


def compute_escalated_backoff_seconds(
    escalation_step: int, base_seconds: float, max_seconds: float
) -> float:
    """Exponential backoff (seconds) for the given 1-indexed
    ``escalation_step`` -- the same doubling shape
    ``retry_backoff.compute_delay`` already has; see this module's own
    docstring (point 3) for why it is duplicated here rather than
    imported.

    Raises:
        ValueError: if ``escalation_step`` is below 1.
    """
    if escalation_step < 1:
        raise ValueError(f"escalation_step must be >= 1, got {escalation_step}")
    delay = base_seconds * (2.0 ** (escalation_step - 1))
    return min(delay, max_seconds)


def target_domain_key(request: Request) -> str | None:
    """Always-applicable level: the target's own domain -- identical
    extraction to ``CircuitBreakerMiddleware._domain``."""
    return urlparse(request.url).netloc or None


def account_id_key(request: Request) -> str | None:
    """Optional level (this module's own docstring, point 1): ``None``
    (skip) unless a caller has explicitly set ``request.meta["account_id"]``
    -- no current spider/config does, on purpose."""
    value = request.meta.get("account_id")
    return str(value) if value else None


def egress_ip_key(request: Request) -> str | None:
    """Optional level, forward-compatible with a future proxy layer
    (Phase 6, still deferred) setting ``request.meta["egress_ip"]``."""
    value = request.meta.get("egress_ip")
    return str(value) if value else None


def egress_asn_key(request: Request) -> str | None:
    """Optional level, forward-compatible with a future proxy layer
    setting ``request.meta["egress_asn"]`` (an ASN or subnet identifier
    string -- deliberately one combined level, not two, since a real
    proxy provider is the one place both would ever be known together)."""
    value = request.meta.get("egress_asn")
    return str(value) if value else None


def _consecutive_intervals(timestamps: Sequence[float]) -> list[float]:
    return [later - earlier for earlier, later in zip(timestamps, timestamps[1:], strict=False)]


@dataclass(frozen=True)
class RateLimitLevel:
    """One independently configurable rate-limit level -- see this
    module's own docstring, point 1."""

    name: str
    key_fn: Callable[[Request], str | None]
    max_requests: int
    window_seconds: float

    def __post_init__(self) -> None:
        if self.max_requests < 1:
            raise ValueError(
                f"max_requests must be >= 1, got {self.max_requests} (level={self.name!r})"
            )
        if self.window_seconds <= 0:
            raise ValueError(
                f"window_seconds must be > 0, got {self.window_seconds} (level={self.name!r})"
            )


def default_levels() -> list[RateLimitLevel]:
    """The four levels this module's own docstring (point 1) describes,
    with the clearly-labeled starting defaults from this module's own
    header constants -- what :meth:`RateLimiterMiddleware.from_crawler`
    builds unless a caller passes ``levels`` explicitly."""
    return [
        RateLimitLevel(
            "target", target_domain_key, DEFAULT_TARGET_MAX_REQUESTS, DEFAULT_TARGET_WINDOW_SECONDS
        ),
        RateLimitLevel(
            "account",
            account_id_key,
            DEFAULT_ACCOUNT_MAX_REQUESTS,
            DEFAULT_ACCOUNT_WINDOW_SECONDS,
        ),
        RateLimitLevel("ip", egress_ip_key, DEFAULT_IP_MAX_REQUESTS, DEFAULT_IP_WINDOW_SECONDS),
        RateLimitLevel(
            "asn_subnet", egress_asn_key, DEFAULT_ASN_MAX_REQUESTS, DEFAULT_ASN_WINDOW_SECONDS
        ),
    ]


class _RateScope:
    """Per-(level, key) mutable state -- one instance per scope actually
    seen, created lazily (same lazy-dict shape
    ``CircuitBreakerMiddleware._circuits`` already uses)."""

    __slots__ = ("request_times", "violation_count", "blocked_until", "last_violation_at")

    def __init__(self) -> None:
        self.request_times: deque[float] = deque()
        self.violation_count = 0
        # Only meaningful while > now; 0.0 otherwise (never blocked yet).
        self.blocked_until = 0.0
        self.last_violation_at = 0.0

    def trim(self, now: float, window_seconds: float) -> None:
        cutoff = now - window_seconds
        while self.request_times and self.request_times[0] < cutoff:
            self.request_times.popleft()


class RateLimiterMiddleware:
    """Scrapy downloader middleware implementing multi-level,
    pattern-aware rate limiting with escalating backoff. See this
    module's own docstring for the full design and rationale.
    """

    def __init__(
        self,
        levels: Sequence[RateLimitLevel] | None = None,
        regularity_min_intervals: int = DEFAULT_REGULARITY_MIN_INTERVALS,
        regularity_cv_threshold: float = DEFAULT_REGULARITY_CV_THRESHOLD,
        violation_threshold: int = DEFAULT_VIOLATION_THRESHOLD,
        backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS,
        backoff_max_seconds: float = DEFAULT_BACKOFF_MAX_SECONDS,
        violation_reset_seconds: float = DEFAULT_VIOLATION_RESET_SECONDS,
        clock: Callable[[], float] | None = None,
        logger: Logger | None = None,
        alert_dispatcher: AlertDispatcher | None = None,
    ) -> None:
        if regularity_min_intervals < 2:
            raise ValueError(
                f"regularity_min_intervals must be >= 2, got {regularity_min_intervals}"
            )
        if regularity_cv_threshold <= 0:
            raise ValueError(
                f"regularity_cv_threshold must be > 0, got {regularity_cv_threshold}"
            )
        if violation_threshold < 1:
            raise ValueError(f"violation_threshold must be >= 1, got {violation_threshold}")
        if backoff_base_seconds <= 0:
            raise ValueError(f"backoff_base_seconds must be > 0, got {backoff_base_seconds}")
        if backoff_max_seconds < backoff_base_seconds:
            raise ValueError(
                f"backoff_max_seconds ({backoff_max_seconds}) must be >= "
                f"backoff_base_seconds ({backoff_base_seconds})"
            )
        if violation_reset_seconds <= 0:
            raise ValueError(
                f"violation_reset_seconds must be > 0, got {violation_reset_seconds}"
            )

        self.levels = list(levels) if levels is not None else default_levels()
        self.regularity_min_intervals = regularity_min_intervals
        self.regularity_cv_threshold = regularity_cv_threshold
        self.violation_threshold = violation_threshold
        self.backoff_base_seconds = backoff_base_seconds
        self.backoff_max_seconds = backoff_max_seconds
        self.violation_reset_seconds = violation_reset_seconds
        self._clock = clock or time.monotonic
        self.logger = logger or get_logger(__name__)
        self._alert_dispatcher = alert_dispatcher or AlertDispatcher()
        self._scopes: dict[tuple[str, str], _RateScope] = {}

    @classmethod
    def from_crawler(cls, crawler: Any) -> RateLimiterMiddleware:
        settings = crawler.settings
        levels = [
            RateLimitLevel(
                "target",
                target_domain_key,
                settings.getint(
                    "TITAN_RATE_LIMIT_TARGET_MAX_REQUESTS", DEFAULT_TARGET_MAX_REQUESTS
                ),
                settings.getfloat(
                    "TITAN_RATE_LIMIT_TARGET_WINDOW_SECONDS", DEFAULT_TARGET_WINDOW_SECONDS
                ),
            ),
            RateLimitLevel(
                "account",
                account_id_key,
                settings.getint(
                    "TITAN_RATE_LIMIT_ACCOUNT_MAX_REQUESTS", DEFAULT_ACCOUNT_MAX_REQUESTS
                ),
                settings.getfloat(
                    "TITAN_RATE_LIMIT_ACCOUNT_WINDOW_SECONDS", DEFAULT_ACCOUNT_WINDOW_SECONDS
                ),
            ),
            RateLimitLevel(
                "ip",
                egress_ip_key,
                settings.getint("TITAN_RATE_LIMIT_IP_MAX_REQUESTS", DEFAULT_IP_MAX_REQUESTS),
                settings.getfloat(
                    "TITAN_RATE_LIMIT_IP_WINDOW_SECONDS", DEFAULT_IP_WINDOW_SECONDS
                ),
            ),
            RateLimitLevel(
                "asn_subnet",
                egress_asn_key,
                settings.getint("TITAN_RATE_LIMIT_ASN_MAX_REQUESTS", DEFAULT_ASN_MAX_REQUESTS),
                settings.getfloat(
                    "TITAN_RATE_LIMIT_ASN_WINDOW_SECONDS", DEFAULT_ASN_WINDOW_SECONDS
                ),
            ),
        ]
        return cls(
            levels=levels,
            regularity_min_intervals=settings.getint(
                "TITAN_RATE_LIMIT_REGULARITY_MIN_INTERVALS", DEFAULT_REGULARITY_MIN_INTERVALS
            ),
            regularity_cv_threshold=settings.getfloat(
                "TITAN_RATE_LIMIT_REGULARITY_CV_THRESHOLD", DEFAULT_REGULARITY_CV_THRESHOLD
            ),
            violation_threshold=settings.getint(
                "TITAN_RATE_LIMIT_VIOLATION_THRESHOLD", DEFAULT_VIOLATION_THRESHOLD
            ),
            backoff_base_seconds=settings.getfloat(
                "TITAN_RATE_LIMIT_BACKOFF_BASE_SECONDS", DEFAULT_BACKOFF_BASE_SECONDS
            ),
            backoff_max_seconds=settings.getfloat(
                "TITAN_RATE_LIMIT_BACKOFF_MAX_SECONDS", DEFAULT_BACKOFF_MAX_SECONDS
            ),
            violation_reset_seconds=settings.getfloat(
                "TITAN_RATE_LIMIT_VIOLATION_RESET_SECONDS", DEFAULT_VIOLATION_RESET_SECONDS
            ),
            alert_dispatcher=dispatcher_from_settings(settings),
        )

    def _scope_for(self, level_name: str, key: str) -> _RateScope:
        scope_key = (level_name, key)
        if scope_key not in self._scopes:
            self._scopes[scope_key] = _RateScope()
        return self._scopes[scope_key]

    def process_request(self, request: Request, spider: Any) -> None:
        now = self._clock()

        # Phase 1: existing cooldowns. Read-only (scope lookup only
        # creates a fresh, all-zero _RateScope if none existed -- no
        # meaningful mutation) -- safe to raise immediately on the first
        # level still in cooldown, nothing to roll back.
        applicable: list[tuple[RateLimitLevel, str, _RateScope]] = []
        for level in self.levels:
            key = level.key_fn(request)
            if key is None:
                continue
            scope = self._scope_for(level.name, key)
            if scope.blocked_until > now:
                remaining = scope.blocked_until - now
                self.logger.error(
                    "rate_limiter.blocked",
                    extra={
                        "level": level.name,
                        "key": key,
                        "remaining_seconds": remaining,
                    },
                )
                raise IgnoreRequest(
                    f"rate_limiter: {level.name}={key} still in cooldown "
                    f"({remaining:.1f}s remaining)"
                )
            applicable.append((level, key, scope))

        # Phase 2: violation detection, purely read-only against each
        # level's *current* state -- this request's own timestamp is not
        # recorded anywhere yet, so a level here never influences another
        # level's own check within this same call.
        violations: list[tuple[RateLimitLevel, str, _RateScope, str]] = []
        for level, key, scope in applicable:
            scope.trim(now, level.window_seconds)
            would_exceed = len(scope.request_times) >= level.max_requests
            intervals = _consecutive_intervals([*scope.request_times, now])
            too_regular = is_pattern_too_regular(
                intervals, self.regularity_min_intervals, self.regularity_cv_threshold
            )
            if would_exceed or too_regular:
                reason = "count_exceeded" if would_exceed else "pattern_detected"
                violations.append((level, key, scope, reason))

        # Phase 2.5: let a sufficiently long clean streak reset a stale
        # violation count *before* this round's own violations (if any)
        # are recorded -- for every applicable scope, not just the ones
        # that stay clean this round. Without this, a scope that was
        # genuinely quiet for well over violation_reset_seconds and then
        # violates again right after would incorrectly keep incrementing
        # its old, stale count instead of starting a fresh streak at 1 --
        # the point of a reset is exactly to forgive a violation that
        # happened long enough ago, independent of what happens next.
        for level, key, scope in applicable:
            if (
                scope.violation_count > 0
                and now - scope.last_violation_at >= self.violation_reset_seconds
            ):
                self.logger.info(
                    "rate_limiter.violations_reset",
                    extra={"level": level.name, "key": key},
                )
                scope.violation_count = 0

        # Phase 3: commit. A request that ends up dropped must never
        # pollute any level's own window (this module's own docstring,
        # "Correctness detail") -- so the hard-block decision for *every*
        # violated level is made before any request_times mutation.
        hard_block: tuple[RateLimitLevel, str, float, str, int] | None = None
        for level, key, scope, reason in violations:
            scope.violation_count += 1
            scope.last_violation_at = now
            if scope.violation_count < self.violation_threshold:
                self.logger.warning(
                    "rate_limiter.violation",
                    extra={
                        "level": level.name,
                        "key": key,
                        "reason": reason,
                        "violation_count": scope.violation_count,
                    },
                )
                continue

            escalation_step = scope.violation_count - self.violation_threshold + 1
            delay = compute_escalated_backoff_seconds(
                escalation_step, self.backoff_base_seconds, self.backoff_max_seconds
            )
            scope.blocked_until = now + delay
            self.logger.error(
                "rate_limiter.escalated",
                extra={
                    "level": level.name,
                    "key": key,
                    "reason": reason,
                    "violation_count": scope.violation_count,
                    "cooldown_seconds": delay,
                },
            )
            self._alert_dispatcher.send(
                AlertEvent(
                    source="rate_limiter",
                    domain=f"{level.name}:{key}",
                    reason=reason,
                    consecutive_failures=scope.violation_count,
                    cooldown_seconds=delay,
                    occurred_at=datetime.now(tz=UTC),
                )
            )
            if hard_block is None:
                hard_block = (level, key, delay, reason, scope.violation_count)

        if hard_block is not None:
            level, key, delay, reason, violation_count = hard_block
            raise IgnoreRequest(
                f"rate_limiter: {level.name}={key} blocked for {delay:.1f}s "
                f"after {violation_count} violations ({reason})"
            )

        # Commit: the request is actually going out (no level escalated
        # to a hard block this round) -- every applicable level, violated
        # -but-still-under-threshold or fully clean, records this
        # timestamp against its own window.
        for _level, _key, scope in applicable:
            scope.request_times.append(now)
        return None
