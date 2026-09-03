"""Cumulative session trust score -- docs/REQUIREMENTS.md, Phase 3 item 1,
an explicit extension of entry 22's own ``RateLimiterMiddleware``
(``src/middlewares/rate_limiter.py``): every existing detection layer in
this mock-target (BotD, fpscanner, JA4, referer/session scoring) judges
one request in isolation and only ever logs, never enforces. Real
systems accumulate evidence across a whole session and escalate their
response gradually -- this module is the first layer here that actually
does both.

**"Trust score" follows the user's own explicit wording literally**: 0
at the start of a session, growing toward 100 as more suspicious
evidence accumulates -- i.e. a higher number means *less* trusted, not
more (the name is the spec's own, kept verbatim rather than renamed to
"suspicion score" to avoid drifting from what was asked for).

**Three independent, real signals**, each contributing points once its
own condition is met by a given request:

1. **Mechanically regular request timing.** The exact same
   coefficient-of-variation formula ``src/middlewares/rate_limiter.py``
   already uses for the identical reason (``coefficient_of_variation``/
   ``is_pattern_too_regular`` below are a deliberate, standalone
   reimplementation -- this app has no dependency on the ``src``
   package and never will, see ``config.py``'s own module docstring for
   why test-environment stays fully independent). A CV near zero across
   at least ``min_interval_samples`` consecutive intervals is the same
   "mechanically fixed cadence" signature that module already catches
   on our own *outgoing* side; here it's the same math applied to
   *incoming* request timing instead.
2. **A missing Referer past the first request of a session.**
   Mirrors ``security/referer_session_integration.py``'s own Level 1
   reasoning exactly: a missing Referer on a session's very first hit
   is completely normal (a real browser's first navigation has none
   either), so only an absence on every request *after* the first
   counts here.
3. **The same User-Agent repeated past ``fingerprint_repeat_threshold``
   times in the tracking window.** A real browser also sends an
   unchanging User-Agent for its whole session -- the actual tell is
   this signal firing *together with* the other two (steady timing,
   never a real Referer), not in isolation; this module's own point
   weights are tuned with that in mind (see :class:`TrustScoreTracker`'s
   own constructor defaults).

**Escalating enforcement, not a single verdict**: three tiers
(``RATE_LIMITED`` -> ``CHALLENGE`` -> ``BLOCKED``), matching real
anti-bot systems' own graduated response (docs/REQUIREMENTS.md entry
19's own cited sources: "no single signal is proof... escalate
gradually") -- never a direct jump from "allowed" to "blocked".
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum


def coefficient_of_variation(intervals: Sequence[float]) -> float | None:
    """Population stddev / mean of ``intervals``. Mirrors
    ``src/middlewares/rate_limiter.py``'s own function of the identical
    name -- ``None`` (undefined, not zero) when there are fewer than 2
    samples or the mean is not strictly positive.
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
    and their coefficient of variation is below ``cv_threshold``. Mirrors
    ``src/middlewares/rate_limiter.py``'s own function of the identical
    name and behavior.

    Raises:
        ValueError: if ``min_intervals`` is below 2, or ``cv_threshold``
            is not strictly positive.
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


TRUST_SESSION_COOKIE_NAME = "mocktarget_trust_session"


class TrustScoreTier(Enum):
    ALLOWED = "allowed"
    RATE_LIMITED = "rate_limited"
    CHALLENGE = "challenge"
    BLOCKED = "blocked"


@dataclass
class TrustScoreResult:
    score: int
    tier: TrustScoreTier
    retry_after_seconds: int | None = None


@dataclass
class _ClientState:
    request_times: list[float] = field(default_factory=list)
    last_user_agent: str | None = None
    same_user_agent_streak: int = 0
    score: int = 0
    seen_before: bool = False


class TrustScoreTracker:
    """Per-session (see ``app.py``'s own ``/trust-scored`` route for how
    the session key is derived -- a real cookie, not a raw IP, so
    concurrent live tests against the same running container stay
    isolated automatically), in-memory, one instance per app lifetime --
    same shape as ``FeedRateLimiter``/``SessionStore`` elsewhere in this
    package.
    """

    def __init__(
        self,
        rate_limit_threshold: int = 30,
        challenge_threshold: int = 60,
        block_threshold: int = 85,
        window_seconds: float = 60.0,
        min_interval_samples: int = 3,
        regularity_cv_threshold: float = 0.15,
        fingerprint_repeat_threshold: int = 5,
        timing_points: int = 15,
        referer_points: int = 10,
        fingerprint_points: int = 20,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if rate_limit_threshold <= 0:
            raise ValueError(f"rate_limit_threshold must be > 0, got {rate_limit_threshold}")
        if challenge_threshold <= rate_limit_threshold:
            raise ValueError("challenge_threshold must be > rate_limit_threshold")
        if block_threshold <= challenge_threshold:
            raise ValueError("block_threshold must be > challenge_threshold")
        if window_seconds <= 0:
            raise ValueError(f"window_seconds must be > 0, got {window_seconds}")

        self._rate_limit_threshold = rate_limit_threshold
        self._challenge_threshold = challenge_threshold
        self._block_threshold = block_threshold
        self._window_seconds = window_seconds
        self._min_interval_samples = min_interval_samples
        self._regularity_cv_threshold = regularity_cv_threshold
        self._fingerprint_repeat_threshold = fingerprint_repeat_threshold
        self._timing_points = timing_points
        self._referer_points = referer_points
        self._fingerprint_points = fingerprint_points
        self._clock = clock or time.monotonic
        self._clients: dict[str, _ClientState] = {}

    def record_request(
        self, client_key: str, user_agent: str | None, referer: str | None
    ) -> TrustScoreResult:
        """Record one request for ``client_key`` and return its current
        cumulative score/tier -- the score only ever grows (or holds
        steady), never decays within a session; a fresh session (a new
        ``client_key``) always starts back at 0.

        Raises:
            ValueError: if ``client_key`` is empty.
        """
        if not client_key:
            raise ValueError("client_key must be non-empty")

        now = self._clock()
        state = self._clients.setdefault(client_key, _ClientState())

        window_start = now - self._window_seconds
        state.request_times = [t for t in state.request_times if t >= window_start]
        state.request_times.append(now)

        is_first_request = not state.seen_before
        state.seen_before = True

        # Signal 1: mechanically regular timing.
        if len(state.request_times) > self._min_interval_samples:
            # strict=False: request_times[1:] is deliberately one shorter
            # than request_times itself -- this is the standard pairwise
            # idiom for consecutive differences, not a length mismatch bug.
            pairs = zip(state.request_times, state.request_times[1:], strict=False)
            intervals = [b - a for a, b in pairs]
            if is_pattern_too_regular(
                intervals, self._min_interval_samples, self._regularity_cv_threshold
            ):
                state.score += self._timing_points

        # Signal 2: missing Referer, past the session's first request.
        if not is_first_request and not referer:
            state.score += self._referer_points

        # Signal 3: the same User-Agent repeated past the threshold.
        if user_agent and user_agent == state.last_user_agent:
            state.same_user_agent_streak += 1
        else:
            state.same_user_agent_streak = 1
        state.last_user_agent = user_agent
        if state.same_user_agent_streak > self._fingerprint_repeat_threshold:
            state.score += self._fingerprint_points

        state.score = min(state.score, 100)

        tier = self._tier_for(state.score)
        retry_after_seconds = (
            int(self._window_seconds) if tier is TrustScoreTier.RATE_LIMITED else None
        )
        return TrustScoreResult(
            score=state.score, tier=tier, retry_after_seconds=retry_after_seconds
        )

    def _tier_for(self, score: int) -> TrustScoreTier:
        if score >= self._block_threshold:
            return TrustScoreTier.BLOCKED
        if score >= self._challenge_threshold:
            return TrustScoreTier.CHALLENGE
        if score >= self._rate_limit_threshold:
            return TrustScoreTier.RATE_LIMITED
        return TrustScoreTier.ALLOWED
