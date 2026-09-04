"""Cross-Signal Consistency -- docs/REQUIREMENTS.md section 9 entry 32
(Phase 3 item 2, the redefined form: JA4 was ruled out as a signal for
Camoufox with conclusive, primary-source evidence -- entry 19, re-
confirmed live in entry 32 -- so this uses three signals that already
exist independently on this mock-target instead).

**The question this module answers is not "is this automated" (each
of the three signals below already answers a version of that on its
own) -- it's "do these three independent signals agree with each
other".** A real detection system gains real information from
*disagreement* between independent signals, not just from any one
signal's own verdict: a client that looks clean on fingerprinting but
whose own request cadence is mechanically perfect is a stronger tell
than either signal alone -- inconsistency itself is the evidence
(same "no single signal is proof on its own" principle
security/fpscanner_integration.py's own module docstring already
documents and cites, applied one level up: across signals, not just
within one).

**The three signals, each already independently built and log-only
(this module changes none of them):**

1. **BotD's verdict** (``security/botd_integration.py``) -- a real,
   vendored third-party detection library's own ``bot`` flag.
2. **fpscanner's score** (``security/fpscanner_integration.py``,
   entry 19) -- WebGL-absence + viewport/screen inconsistency, 0-2.
3. **Request-timing regularity** -- the same coefficient-of-variation
   approach ``src/middlewares/rate_limiter.py`` and, on this app's own
   side, ``structural/trust_score.py`` (entry 31) already use,
   reimplemented standalone here a third time rather than imported
   (the same "small, focused duplication over cross-module coupling"
   tradeoff both of those modules' own docstrings already document for
   this exact function -- keeping ``security/`` and ``structural/``
   decoupled siblings, the same shape this app's whole package already
   has).

**Log-only, a count of disagreeing pairs, never a hard verdict** --
identical philosophy to fpscanner_integration.py's own
``score_fingerprint_report``: this module never enforces anything by
itself, and the score is a *count* (0-3), never a boolean.
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from collections.abc import Callable, Sequence
from typing import Any

from security.fpscanner_integration import score_fingerprint_report

DEFAULT_WINDOW_SECONDS = 60.0
DEFAULT_MIN_INTERVAL_SAMPLES = 3
DEFAULT_REGULARITY_CV_THRESHOLD = 0.15


def coefficient_of_variation(intervals: Sequence[float]) -> float | None:
    """Population stddev / mean of ``intervals``. Mirrors
    ``src/middlewares/rate_limiter.py``'s (and
    ``structural/trust_score.py``'s) own function of the identical
    name -- ``None`` when there are fewer than 2 samples or the mean is
    not strictly positive.
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
    """``True`` when ``intervals`` has at least ``min_intervals``
    samples and their coefficient of variation is below
    ``cv_threshold``. Mirrors ``src/middlewares/rate_limiter.py``'s
    (and ``structural/trust_score.py``'s) own function of the
    identical name and behavior.

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


class SessionTimingTracker:
    """Per-session request-timestamp history, purely for the timing-
    regularity signal above -- deliberately NOT ``structural/
    trust_score.py``'s own ``TrustScoreTracker`` (that one bundles
    scoring *and* enforcement across three different signals of its
    own; this needs just the one, read-only, for a different purpose
    downstream). One instance per app lifetime, same lazy-per-key shape
    every other stateful tracker in this package already has.
    """

    def __init__(
        self,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        min_interval_samples: int = DEFAULT_MIN_INTERVAL_SAMPLES,
        regularity_cv_threshold: float = DEFAULT_REGULARITY_CV_THRESHOLD,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if window_seconds <= 0:
            raise ValueError(f"window_seconds must be > 0, got {window_seconds}")

        self._window_seconds = window_seconds
        self._min_interval_samples = min_interval_samples
        self._regularity_cv_threshold = regularity_cv_threshold
        self._clock = clock or time.monotonic
        self._sessions: dict[str, deque[float]] = {}

    def record_and_check(self, session_key: str) -> bool | None:
        """Record one request for ``session_key`` *and* return whether
        this session's recent cadence is mechanically regular --
        ``None`` (not ``False``) when there aren't enough samples yet
        to judge, the same "undefined, not innocent" distinction
        ``TrustScoreTracker``'s own signal 1 makes.

        Raises:
            ValueError: if ``session_key`` is empty.
        """
        if not session_key:
            raise ValueError("session_key must be non-empty")

        now = self._clock()
        times = self._sessions.setdefault(session_key, deque())
        window_start = now - self._window_seconds
        while times and times[0] < window_start:
            times.popleft()
        times.append(now)

        if len(times) <= self._min_interval_samples:
            return None
        intervals = [b - a for a, b in zip(times, list(times)[1:], strict=False)]
        return is_pattern_too_regular(
            intervals, self._min_interval_samples, self._regularity_cv_threshold
        )


def compute_inconsistency_score(
    botd_result: dict[str, Any], fingerprint_report: dict[str, Any], timing_is_regular: bool | None
) -> int:
    """Pure function -- no I/O, independently unit-testable. Derives
    each signal's own "looks automated" verdict, then counts how many
    of the up-to-3 pairs *disagree* with each other. A pair involving
    an unknown (``None``) timing verdict is skipped entirely (not
    counted as either agreement or disagreement -- "not enough data
    yet" is not evidence of inconsistency).

    - BotD: ``botd_result.get("bot")`` truthy.
    - fpscanner: :func:`score_fingerprint_report` >= 1 (at least one of
      its own two independent signals fired).
    - timing: ``timing_is_regular`` itself.

    Raises:
        TypeError: if ``botd_result`` or ``fingerprint_report`` isn't a
            dict (propagated from :func:`score_fingerprint_report` for
            the latter; checked directly for the former, matching
            ``security/botd_integration.py``'s own ``log_botd_report``).
    """
    if not isinstance(botd_result, dict):
        raise TypeError(f"botd_result must be a dict, got {type(botd_result).__name__}")

    botd_flag = bool(botd_result.get("bot"))
    fpscanner_flag = score_fingerprint_report(fingerprint_report) >= 1

    verdicts: list[bool] = [botd_flag, fpscanner_flag]
    if timing_is_regular is not None:
        verdicts.append(timing_is_regular)

    score = 0
    for i in range(len(verdicts)):
        for j in range(i + 1, len(verdicts)):
            if verdicts[i] != verdicts[j]:
                score += 1
    return score


def log_cross_signal_check(
    logger: logging.Logger,
    botd_result: dict[str, Any],
    fingerprint_report: dict[str, Any],
    timing_is_regular: bool | None,
) -> int:
    """Logs one combined cross-signal check at INFO, always -- same
    log-only, no-threshold-yet philosophy as
    ``security/fpscanner_integration.py``'s own ``log_fingerprint_report``.
    Returns the computed score so a caller (the Flask route) can echo
    it back without recomputing.

    Raises:
        TypeError: propagated from :func:`compute_inconsistency_score`.
    """
    score = compute_inconsistency_score(botd_result, fingerprint_report, timing_is_regular)
    logger.info(
        "cross_signal.checked",
        extra={
            "botd_result": botd_result,
            "fingerprint_report": fingerprint_report,
            "timing_is_regular": timing_is_regular,
            "inconsistency_score": score,
        },
    )
    return score
