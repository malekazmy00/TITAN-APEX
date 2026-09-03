"""Integration test: test-environment/mock-target/structural/trust_score.py's
``/trust-scored`` route against the real, live test-environment/ stack --
docs/REQUIREMENTS.md section 9 entry 22.1 (Phase 3 item 1, an explicit
extension of entry 22 -- ``src/middlewares/rate_limiter.py``'s
``RateLimiterMiddleware``).

The problem this entry is about, verbatim from the user's own request:
every existing test in this project is pass/fail against a *single*
request; real anti-bot systems accumulate evidence across a whole
session and escalate gradually. This is the first live test in this
package that proves that against real, observed numbers -- not a
description.

Deliberately NOT a ``scrapy runspider`` subprocess run (unlike most
mock-target-* live tests in this package) -- same reasoning as
``test_response_classifier_live.py``'s own module docstring: precise,
per-request timing control (a perfectly fixed cadence for the
worst-case scenario, a wide random jitter for the natural one) and
per-request header introspection (``X-Trust-Score``/``X-Trust-Tier``)
are the thing under test here, not Scrapy's own request scheduling --
a real ``urllib.request`` call against the live stack (the same stdlib
this project's own ``byparr_provider.py`` already uses for real HTTP
calls) gives that control directly, with a real ``http.cookiejar``
carrying the session cookie across calls exactly like a real browser
(or Scrapy's own always-enabled ``CookiesMiddleware``) would.

**Two real scenarios, run once (module-scoped fixture) and shared by
both test functions below, each against its own fresh cookie jar (so
the two scenarios' sessions never mix, matching
``TrustScoreTracker``'s own session-cookie-keyed design):**

1. **Fixed-timing worst case.** No Referer, ever (after the first
   request); a mechanically fixed 0.05s delay between every request.
2. **Natural jitter.** A real, logical Referer on every request after
   the first (each hop points back at the URL immediately before it --
   the "logical Referer" signal the module docstring describes); a
   randomized delay per request (``random.uniform`` over a wide
   0.05s-1.5s range -- CV for a uniform distribution that wide is
   ~0.55, far above ``regularity_cv_threshold``'s default 0.15, so this
   reliably never trips the timing signal).

Both scenarios deliberately send the exact same, unchanging
User-Agent throughout (a real session does too -- see this module's
own docstring on why a same-UA-repeated signal for a *reasonable*
number of hits is a real design property to observe, not something to
mask by rotating the UA mid-test) -- the only two things that differ
between the scenarios are Referer presence and request-timing
regularity, so any difference in how fast each escalates is
attributable to those two signals specifically.

**Real numbers observed, not assumed.** The exact request index at
which each tier is first reached is server config (thresholds/points)
-dependent, not hardcoded here as an assumed constant -- both
scenarios' full per-request (score, tier) trail are captured and
printed unconditionally (same discipline
``tests/integration/test_playwright_live_render.py`` was just given,
entry 30.2) so a CI log always carries the real, first-hand evidence
this entry's own success criterion asks for: "الفرق الكمي بين
السيناريوهين موثّق بالأرقام، مش وصف عام".

Requires TITAN_BYPARR_URL *and* a running
test-environment/docker-compose.test.yml stack reachable at
http://localhost:8080/ -- same "is a live-network CI stack actually
running" gate every other mock-target live test in this package uses.
"""

from __future__ import annotations

import http.cookiejar
import os
import random
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest

BYPARR_URL = os.environ.get("TITAN_BYPARR_URL")
MOCK_TARGET_BASE_URL = "http://localhost:8080"
TRUST_SCORED_URL = f"{MOCK_TARGET_BASE_URL}/trust-scored"

REQUEST_COUNT = 55  # comfortably above the user's own "50+" requirement
FIXED_DELAY_SECONDS = 0.05
JITTER_MIN_SECONDS = 0.05
JITTER_MAX_SECONDS = 1.5

TIER_RANK = {"allowed": 0, "rate_limited": 1, "challenge": 2, "blocked": 3}


@dataclass
class _RequestObservation:
    index: int  # 1-indexed
    status: int
    score: int
    tier: str


@dataclass
class _ScenarioResult:
    name: str
    observations: list[_RequestObservation] = field(default_factory=list)

    def first_index_at_or_above(self, tier: str) -> int | None:
        target_rank = TIER_RANK[tier]
        for obs in self.observations:
            if TIER_RANK[obs.tier] >= target_rank:
                return obs.index
        return None

    def score_at(self, index: int) -> int:
        for obs in self.observations:
            if obs.index == index:
                return obs.score
        raise AssertionError(f"no observation recorded for request index {index}")


def _open_new_session() -> urllib.request.OpenerDirector:
    """A fresh cookie jar == a fresh trust-score identity server-side --
    see this module's own docstring."""
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def _fetch_trust_scored(
    opener: urllib.request.OpenerDirector, referer: str | None, user_agent: str
) -> _RequestObservation:
    headers = {"User-Agent": user_agent}
    if referer is not None:
        headers["Referer"] = referer
    req = urllib.request.Request(TRUST_SCORED_URL, headers=headers)  # noqa: S310
    try:
        with opener.open(req, timeout=30) as resp:  # noqa: S310
            status = resp.status
            resp_headers = dict(resp.headers)
    except urllib.error.HTTPError as exc:
        # 429/403 are real, expected HTTPErrors from urllib's own
        # perspective (any non-2xx raises) -- exactly the escalation
        # tiers this test exists to observe, not a failure to fetch.
        status = exc.code
        resp_headers = dict(exc.headers)
    return _RequestObservation(
        index=0,  # filled in by the caller, which knows the real request count
        status=status,
        score=int(resp_headers["X-Trust-Score"]),
        tier=resp_headers["X-Trust-Tier"],
    )


def _run_fixed_timing_worst_case() -> _ScenarioResult:
    opener = _open_new_session()
    result = _ScenarioResult(name="fixed_timing_worst_case")
    for i in range(1, REQUEST_COUNT + 1):
        obs = _fetch_trust_scored(opener, referer=None, user_agent="titan-apex-live-test-fixed/1.0")
        obs.index = i
        result.observations.append(obs)
        time.sleep(FIXED_DELAY_SECONDS)
    return result


def _run_natural_jitter() -> _ScenarioResult:
    opener = _open_new_session()
    result = _ScenarioResult(name="natural_jitter")
    previous_url: str | None = None
    for i in range(1, REQUEST_COUNT + 1):
        obs = _fetch_trust_scored(
            opener, referer=previous_url, user_agent="titan-apex-live-test-jittered/1.0"
        )
        obs.index = i
        result.observations.append(obs)
        previous_url = TRUST_SCORED_URL
        time.sleep(random.uniform(JITTER_MIN_SECONDS, JITTER_MAX_SECONDS))  # noqa: S311
    return result


def _print_trail(result: _ScenarioResult) -> None:
    # Printed unconditionally -- same discipline entry 30.2 established
    # for test_playwright_live_render.py: real diagnostic evidence in
    # every CI log, not just on a failure.
    print(f"--- trust-score live trail: {result.name} ---")
    for obs in result.observations:
        print(f"  request {obs.index:>2}: status={obs.status} score={obs.score:>3} tier={obs.tier}")


@pytest.fixture(scope="module")
def crawl_results() -> Iterator[dict[str, _ScenarioResult]]:
    fixed = _run_fixed_timing_worst_case()
    natural = _run_natural_jitter()
    _print_trail(fixed)
    _print_trail(natural)
    yield {"fixed": fixed, "natural": natural}


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no live-network CI stack running)"
)
def test_fixed_timing_worst_case_shows_gradual_escalation_across_a_long_crawl(
    crawl_results: dict[str, _ScenarioResult],
) -> None:
    """Item 3 of this entry, verbatim: a real 50+-sequential-request
    crawl with deliberately fixed, worst-case timing must show the code
    actually noticing *gradual* escalation -- RATE_LIMITED, then
    CHALLENGE, then BLOCKED, each strictly after the last, never a
    direct jump from allowed to blocked, and never a regression once
    reached (this design's score only ever grows within a session --
    structural/trust_score.py's own module docstring)."""
    fixed = crawl_results["fixed"]

    ranks = [TIER_RANK[obs.tier] for obs in fixed.observations]
    tiers_seen = [o.tier for o in fixed.observations]
    assert ranks == sorted(ranks), f"fixed-timing tier sequence regressed: {tiers_seen}"

    rate_limited_at = fixed.first_index_at_or_above("rate_limited")
    challenge_at = fixed.first_index_at_or_above("challenge")
    blocked_at = fixed.first_index_at_or_above("blocked")
    assert rate_limited_at is not None, "worst-case fixed timing never reached RATE_LIMITED"
    assert challenge_at is not None, "worst-case fixed timing never reached CHALLENGE"
    assert blocked_at is not None, "worst-case fixed timing never reached BLOCKED"
    assert rate_limited_at < challenge_at < blocked_at, (
        f"tiers were not reached in gradual order: rate_limited at {rate_limited_at}, "
        f"challenge at {challenge_at}, blocked at {blocked_at}"
    )

    # Once blocked, stays blocked for the rest of this long crawl --
    # real evidence never un-accumulates within a session.
    assert all(obs.tier == "blocked" for obs in fixed.observations[blocked_at - 1 :])


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no live-network CI stack running)"
)
def test_natural_jitter_keeps_the_score_under_threshold_longer_than_the_fixed_timing_worst_case(
    crawl_results: dict[str, _ScenarioResult],
) -> None:
    """Item 4 of this entry, verbatim: the same request count, but with
    natural jitter (plus a real, logical Referer chain) instead of a
    mechanically fixed cadence, must stay under threshold longer than
    the worst-case scenario -- proven here with the real, observed
    request indices and score values from both live crawls
    (``crawl_results``), not a general description."""
    fixed = crawl_results["fixed"]
    natural = crawl_results["natural"]

    fixed_blocked_at = fixed.first_index_at_or_above("blocked")
    natural_blocked_at = natural.first_index_at_or_above("blocked")
    assert fixed_blocked_at is not None, "fixed-timing scenario never reached BLOCKED"

    if natural_blocked_at is None:
        # The natural-jitter session never escalated to BLOCKED at all
        # within this crawl -- the strongest possible form of "stays
        # under threshold longer". Quantify it directly against the
        # fixed scenario's own final score at the same request count.
        natural_final_score = natural.observations[-1].score
        fixed_final_score = fixed.observations[-1].score
        print(
            f"--- quantitative comparison ---\n"
            f"fixed-timing reached BLOCKED at request {fixed_blocked_at} "
            f"(final score {fixed_final_score}); natural-jitter never reached BLOCKED "
            f"in {REQUEST_COUNT} requests (final score {natural_final_score})"
        )
        assert natural_final_score < fixed_final_score
        return

    # Both scenarios eventually got blocked -- the real claim is that
    # natural jitter took strictly longer (a higher request index) to
    # get there, and stayed at a strictly lower score than the
    # fixed-timing scenario at that same request index.
    fixed_score_at_fixed_blocked = fixed.score_at(fixed_blocked_at)
    natural_score_at_same_index = natural.score_at(fixed_blocked_at)
    natural_tier_at_same_index = natural.observations[fixed_blocked_at - 1].tier
    print(
        f"--- quantitative comparison ---\n"
        f"fixed-timing reached BLOCKED at request {fixed_blocked_at} (score "
        f"{fixed_score_at_fixed_blocked}); at that same request, natural-jitter's score was "
        f"only {natural_score_at_same_index} (tier={natural_tier_at_same_index}). "
        f"natural-jitter itself first reached BLOCKED at request {natural_blocked_at}."
    )
    assert natural_blocked_at > fixed_blocked_at, (
        f"expected natural jitter to reach BLOCKED strictly later than the fixed-timing "
        f"worst case ({fixed_blocked_at}), got {natural_blocked_at}"
    )
    assert natural_score_at_same_index < fixed_score_at_fixed_blocked
