"""Referer path-consistency + session-warm-up scoring -- docs/REQUIREMENTS.md
section 9 entry 21, Step 1 (Levels 1/2 only; Level 3's session-wide,
delayed/retroactive classification is a separate, later step -- not
this module's job at all yet).

**Level 1 (`score_referer_shape`):** does the Referer header, if
present, actually look like a real, well-formed absolute URL?
Deliberately does **not** score a *missing* Referer as a violation on
its own -- Scrapfly's own real source (docs/REQUIREMENTS.md entry 21,
directly quoted there) is explicit that "The first page may have no
Referer at all" is the normal, expected start of a real browsing
chain. Whether an absent Referer is actually suspicious depends on
*which* page is being requested (a deep page that structurally expects
a real predecessor, vs. a legitimate entry point) -- that contextual
judgment belongs to Level 2, which is the only level that ever looks at
the request path at all.

**Level 2 (`score_referer_path_consistency`):** for a path that has a
real, expected predecessor in this mock-target's own tiny navigation
graph (`VALID_PREDECESSOR_PATHS` below), does the Referer's own path
actually match one, and is there a warm-up session cookie already
present (real evidence of an earlier visit in this same session, not a
cold, isolated hit)? Both real, independently-sourced signals (docs/
REQUIREMENTS.md entry 21): a Referer that doesn't match any real
predecessor path (including a missing one, on a page that expects one)
is exactly the "wrong/absent value looks unnatural on deep endpoints"
gap Scrapfly documents; a session with no warm-up cookie hitting a deep
page directly is exactly the missing "warm sessions before deep
crawling" step the same entry's webautomation.io source names.

`/warmup-home` (this mock-target's own entry point -- see `app.py`) has
no entry in `VALID_PREDECESSOR_PATHS` at all: it's where a real
session is expected to *start*, so there is no predecessor to require.

Deliberately never a hard verdict, log-only, multi-signal-score -- the
same established design `security/fpscanner_integration.py`'s own
module docstring already documents and cites sources for (entry 19):
no single signal is proof of automation on its own, and enforcement
decisions are deferred until real traffic has actually been studied in
this log-only mode first.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

#: This mock-target's own tiny, real navigation graph -- the actual
#: `/warmup-home` -> `/warmup-category` -> `/warmup-target` chain
#: `app.py` serves (docs/REQUIREMENTS.md section 9 entry 21, Step 1).
#: `/warmup-target` accepts itself as its own predecessor too: a real
#: reload/pagination-style re-visit of the same deep page legitimately
#: carries that same page as its own Referer, not the category page
#: again.
VALID_PREDECESSOR_PATHS: dict[str, frozenset[str]] = {
    "/warmup-category": frozenset({"/warmup-home"}),
    "/warmup-target": frozenset({"/warmup-category", "/warmup-target"}),
}

WARMUP_SESSION_COOKIE_NAME = "mocktarget_warmup_session"


def score_referer_shape(referer: str | None) -> int:
    """Level 1: ``1`` only if ``referer`` is present but not a
    well-formed absolute URL (real scheme + real host) -- see this
    module's own docstring for why a *missing* Referer scores ``0``
    here, deliberately, rather than being treated as inherently
    suspicious."""
    if not referer:
        return 0
    parsed = urlparse(referer)
    if not parsed.scheme or not parsed.netloc:
        return 1
    return 0


def score_referer_path_consistency(
    current_path: str, referer: str | None, has_warmup_session_cookie: bool
) -> int:
    """Level 2: up to ``2`` -- ``1`` if ``current_path`` has a real
    expected predecessor (per ``VALID_PREDECESSOR_PATHS``) and
    ``referer``'s own path isn't one of them (a missing Referer counts
    as a mismatch here, unlike at Level 1: this module's own docstring
    explains why that's contextual, not a shape judgment), and
    independently ``1`` if that same expectation exists and
    ``has_warmup_session_cookie`` is ``False``.

    ``current_path`` values with no entry in ``VALID_PREDECESSOR_PATHS``
    (this mock-target's own real entry point, ``/warmup-home``) always
    score ``0`` -- there is no predecessor to require at all.
    """
    expected = VALID_PREDECESSOR_PATHS.get(current_path)
    if expected is None:
        return 0

    score = 0
    referer_path = urlparse(referer).path if referer else None
    if referer_path not in expected:
        score += 1
    if not has_warmup_session_cookie:
        score += 1
    return score


def log_referer_session_check(
    logger: logging.Logger,
    current_path: str,
    referer: str | None,
    has_warmup_session_cookie: bool,
) -> None:
    """Logs one request's Level 1 + Level 2 scores, always at INFO --
    log-only, same "observe first, decide enforcement later" principle
    ``fpscanner_integration.py``'s own docstring already establishes
    for this exact reason (entry 19's own Microsoft/F5 citations)."""
    level1 = score_referer_shape(referer)
    level2 = score_referer_path_consistency(current_path, referer, has_warmup_session_cookie)
    logger.info(
        "referer_session.checked",
        extra={
            "path": current_path,
            "referer": referer,
            "has_warmup_session_cookie": has_warmup_session_cookie,
            "level1_score": level1,
            "level2_score": level2,
        },
    )
