"""Client-side, multi-signal automation fingerprint scoring --
docs/REQUIREMENTS.md section 9 entry 19 (item 10's fpscanner-based
research, after JA4/TLS was confirmed -- with direct, primary-source
evidence, not assumed -- to have zero discriminating value for
Camoufox specifically: daijro/camoufox issue #555 shows identical
JA3/JA4/Akamai-H2 hashes between stock Firefox and Camoufox, because
Camoufox patches Firefox at the C++/Rust engine level rather than
wrapping JS, so no JS-visible "prototype lie" (the kind CreepJS's own
detection specifically targets) is ever produced by the network-layer
identity at all).

Two independently-evidenced signals, neither of which
security/botd_integration.py's own vendored library
(static/vendor/botd.esm.js, FingerprintJS's real BotD) already covers
-- confirmed by reading that file directly, not assumed:

1. WebGL total absence. Camoufox disables WebGL entirely by default
   (no dataset yet to rotate a fake GPU fingerprint against) --
   confirmed directly against this project's own Camoufox instance:
   ``canvas.getContext('webgl')`` returns ``null``. A real user's
   browser has WebGL available in the overwhelming majority of cases
   in 2026, so its total absence is a real, simple, near-universal
   signal. BotD's own ``detectWebGL()`` only flags a *specific* known
   vendor/renderer string (``'Brian Paul'``/``'Mesa OffScreen'``, an
   old headless-Chrome software-rendering signature) -- it never even
   reaches that string comparison when the context is null entirely
   (its own ``getWebGL()`` throws first, so ``webGL.state`` is never
   ``Success``), which is exactly why BotD has never once flagged a
   real Camoufox run in this project's own history
   (test-environment/logs/botd_flags.log: ``"bot": false`` on every
   single logged report, confirmed by direct inspection).

2. Viewport/screen self-consistency: ``window.innerWidth``/
   ``innerHeight`` must never exceed ``window.screen.width``/
   ``height`` -- a trivial invariant any real display satisfies by
   construction (the visible content area cannot be physically larger
   than the screen it's rendered on). A real, documented Camoufox/
   Playwright inconsistency (the same GitHub issue #555 above also
   reports the automation's own configured viewport disagreeing with
   the JS-reported screen dimensions) -- though the *exact* numbers
   are config/version-dependent, confirmed by hand: this project's own
   Camoufox instance reproduced a *different*-shaped mismatch than the
   cited issue's own example, not the same fixed numbers. This checks
   the general consistency *principle*, never a fixed blacklist of
   "bad" values. BotD's own ``detectWindowSize()`` checks a completely
   different relationship (``window.outerWidth``/``outerHeight`` both
   being exactly zero -- an old, no-window-chrome-at-all headless
   signature) -- confirmed directly that this project's own Camoufox
   reports real, non-zero outer dimensions, so it never trips that
   check either.

Deliberately a *score*, never a single-signal verdict, and this module
never enforces anything by itself -- both are real, independently
documented bot-detection design principles, not this project's own
invention:

- "No single signal is proof of automation on its own" -- real systems
  combine independent weak signals into a risk score precisely because
  any one signal, alone, is wrong some of the time (see
  docs/REQUIREMENTS.md's own citations: Intuned's "how bot detection
  works", Castle's "bot detection 101").
- "Log in monitoring mode first, decide enforcement only after
  studying real traffic" -- Microsoft's and F5's own documented bot-
  management guidance (also cited in docs/REQUIREMENTS.md). This
  module follows security/botd_integration.py's own already-
  established "observe, don't enforce" pattern for exactly this
  reason: no request is ever blocked here, and no WARNING-level
  threshold is set yet either -- that decision is deliberately
  deferred to a later, real-data-informed step.
"""

from __future__ import annotations

import logging
from typing import Any


def score_fingerprint_report(report: dict[str, Any]) -> int:
    """Pure scoring function -- no I/O, no logging, independently
    unit-testable. Each signal contributes at most 1 point; the result
    is a count of how many independent signals fired, deliberately
    never a single-signal hard verdict (this module's own docstring
    has the full reasoning).

    Raises:
        TypeError: if ``report`` isn't a dict -- a malformed report
            can't be meaningfully scored.
    """
    if not isinstance(report, dict):
        raise TypeError(f"report must be a dict, got {type(report).__name__}")

    score = 0
    if report.get("webglAvailable") is False:
        score += 1
    if report.get("viewportConsistent") is False:
        score += 1
    return score


def log_fingerprint_report(logger: logging.Logger, report: dict[str, Any]) -> None:
    """Logs one client-side fingerprint report at INFO, always --
    deliberately never WARNING/ERROR yet (this module's own docstring
    explains why: no enforcement threshold has been decided, this is
    the log-only phase, same as security/botd_integration.py's own
    ``log_botd_report`` was before any classification existed).
    ``score_fingerprint_report``'s own result is included so a human
    reviewing the log can see exactly how many of the two signals
    fired, without this function itself making that judgment call.

    Raises:
        TypeError: if ``report`` isn't a dict (propagated from
            :func:`score_fingerprint_report`).
    """
    score = score_fingerprint_report(report)
    logger.info("fingerprint.report", extra={"report": report, "score": score})
