"""Integration test: src/spiders/configs/mock_target.yaml against the real,
live test-environment/ stack (mock-target behind Anubis's real proof-of-work
reverse proxy) -- test-environment/README.md section 5, docs/REQUIREMENTS.md
section 8's Escalation Cycle.

Requires TITAN_BYPARR_URL *and* a running
test-environment/docker-compose.test.yml stack reachable at
http://localhost:${ANUBIS_PORT:-8080}/ -- the CI workflow brings that stack
up (`docker compose -f test-environment/docker-compose.test.yml up -d
--build`) before running tests/integration; skips cleanly, same as every
other Byparr-dependent live test, if TITAN_BYPARR_URL isn't set (so this
still collects and no-ops in a plain local `pytest tests/unit` run, which
never sets it).

**Escalation round 2 (this version)** -- both gaps registered after round 1
are fixed for real, not assumed fixed:

1. **Network gap (round 1 entry 1): fixed.** `docker-compose.test.yml` now
   runs its own dedicated `byparr` service with
   `network_mode: "service:anubis"` -- it shares Anubis's network
   namespace outright, so its own `localhost:8080` genuinely *is* Anubis's
   listener, no DNS/extra_hosts trick needed. Confirmed by hand: Byparr's
   `/v1` API, called with the exact same `http://localhost:8080/` this
   test has always used, now gets a real `200` with Anubis's actual
   **challenge page** (title "Making sure you're not a bot!") instead of
   `NS_ERROR_CONNECTION_REFUSED` -- the request genuinely reaches Anubis
   and receives a real proof-of-work challenge (weight `+10` for Byparr's
   browser-like User-Agent, Anubis's own log: `"msg":"new challenge issued"`).
2. **Secure-cookie gap (round 1 entry 2): fixed.** Verified against
   Anubis's own source (`cmd/anubis/main.go`): `cookie-secure` (env
   `COOKIE_SECURE`) is a first-class, documented flag, not a workaround --
   Anubis has no built-in TLS server at all, so this is the officially
   supported way to run it without one. Set to `false`, confirmed by hand
   via `curl`'s own `Set-Cookie` response headers: `Secure` is gone, and
   `cookie-same-site` auto-downgrades from `None` to `Lax` exactly as
   Anubis's own flag help text says it will. `COOKIE_PARTITIONED=false`
   was added alongside it after the `Secure` fix alone still wasn't
   enough (Anubis's cookies default to CHIPS-`Partitioned`, which some
   automated browser contexts don't handle the same way a normal profile
   does) -- both flags together, confirmed real and independently by hand.

**A third, new gap surfaced only once both of the above were real** (not
assumed -- found by watching Anubis's own request log for over 20 seconds
after a completed Byparr `solve()` call, hand-driven, several repeated
attempts, not flaky): Byparr's browser still never completes the
challenge, but not for a cookie reason this time. Anubis issues the
challenge, and then **nothing else happens at all** -- no
`pass-challenge` call, no "cookies disabled" warning, no proxied request,
nothing -- for as long as the request stays open. Anubis's real challenge
flow computes the proof-of-work *asynchronously in the browser* after the
page's `load` event fires (a JS routine that POSTs the result once done),
but Byparr's `/v1` API returns as soon as `load` fires and its own log
already shows it navigating with `waiting until "load"` -- it captures
the DOM and tears the browser context down right then, before that async
routine ever gets a chance to run. This is architecturally the same shape
as the `render_wait_ms` gap `PlaywrightMiddleware` already had and fixed
(a site/challenge doing real work *after* the load event, needing an
explicit extra wait) -- except here it would need to live in
`ByparrProvider`/Byparr's own API contract instead, and Byparr's
documented protocol (its own README, checked) exposes no "wait N ms after
load" parameter to add one. Tracked as a new, genuine "Known Gap from Test
Environment" -- not fixed this round.

**Net result:** the crawl still finishes with zero items, but for a
materially different, more advanced reason than round 1 -- it now
genuinely reaches Anubis and receives a real challenge every time, and
just doesn't complete it, rather than never reaching Anubis at all. Per
docs/REQUIREMENTS.md section 8's own principle, that is real progress at
this difficulty level, not proof the gap is closed. See
docs/REQUIREMENTS.md's "Known Gaps from Test Environment" section for the
full, dated writeup.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from tests.integration._live_helpers import run_spider_live

BYPARR_URL = os.environ.get("TITAN_BYPARR_URL")
# This test's own dedicated Byparr instance (docker-compose.test.yml),
# network_mode: service:anubis -- NOT the root docker-compose.yml's
# `services: byparr` on 8191 that every other live test uses. Gated on
# the same TITAN_BYPARR_URL as everything else (the "are we in the live
# CI job" signal this whole suite already uses), but actually talks to
# the compose-local instance on its own port.
MOCK_TARGET_BYPARR_URL = f"http://localhost:{os.environ.get('MOCK_TARGET_BYPARR_PORT', '8193')}"
REPO_ROOT = Path(__file__).resolve().parents[2]
HONEYPOT_LOG = REPO_ROOT / "test-environment" / "logs" / "honeypot_triggers.log"


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no Byparr instance running)"
)
def test_mock_target_yields_zero_items_stuck_behind_anubis_challenge(tmp_path: Path) -> None:
    """Documents the real, current outcome (see module docstring for the
    full, evidenced root-cause analysis): the crawl finishes cleanly, but
    with zero items -- Byparr now genuinely reaches Anubis and receives a
    real proof-of-work challenge every time (round 1's network and cookie
    gaps are fixed), but its browser tears down before Anubis's async
    challenge JS ever runs. This assertion is a regression sentinel, not
    an aspiration -- if this ever changes (a future Byparr wait-after-load
    capability, or a different antibot provider) and the crawl starts
    finding real posts, this test should be updated to match, the same way
    ajax-javascript/load-more were updated once render_wait_ms/click_selector
    genuinely fixed them.
    """
    assert BYPARR_URL  # guarded by skipif above; narrows type for mypy too
    output_path = tmp_path / "mock_target_live.jsonl"

    items = run_spider_live(
        "mock_target.yaml",
        output_path,
        extra_settings={"TITAN_BYPARR_URL": MOCK_TARGET_BYPARR_URL},
    )

    assert items == [], (
        f"expected zero items (see this test's module docstring for the "
        f"real, evidenced reason); got {len(items)}: {items[:1]}"
    )


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no Byparr instance running)"
)
def test_mock_target_crawl_never_reaches_a_real_honeypot(tmp_path: Path) -> None:
    """A honeypot trigger would mean the crawl reached real mock-target
    content and then followed something it shouldn't have. Since the
    crawl above never gets past Anubis's challenge at all, nothing here
    should be logged from this run -- confirmed by requiring the log file
    to be either absent or unchanged in size across the run.
    """
    assert BYPARR_URL  # guarded by skipif above; narrows type for mypy too
    before_size = HONEYPOT_LOG.stat().st_size if HONEYPOT_LOG.exists() else None

    output_path = tmp_path / "mock_target_live_honeypot_check.jsonl"
    run_spider_live(
        "mock_target.yaml",
        output_path,
        extra_settings={"TITAN_BYPARR_URL": MOCK_TARGET_BYPARR_URL},
    )

    after_size = HONEYPOT_LOG.stat().st_size if HONEYPOT_LOG.exists() else None
    assert after_size == before_size, (
        "honeypot_triggers.log grew during a crawl that should never have "
        "reached real mock-target content -- see this module's docstring"
    )
