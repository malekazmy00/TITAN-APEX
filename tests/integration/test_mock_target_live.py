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

Real, reproduced result -- documented in full, not summarized, per section
5's explicit "مش المفروض ينجح من أول مرة بالكامل" (not expected to fully
succeed on the first try). Confirmed locally (3/3 identical attempts, not
flaky) before this test was written, by driving the real
mock-target+Anubis+Byparr container topology by hand:

1. GenericSpider issues one plain GET, meta={"antibot_needed": True}, so
   ByparrMiddleware (not the raw downloader) handles it.
2. Byparr's browser reaches Anubis, whose real, unmodified default policy
   (anubis/botPolicy.yaml) weighs any User-Agent matching `Mozilla|Opera`
   (Byparr's Chromium reports one) at +10 -- the "moderate-suspicion"
   threshold -- and issues a real proof-of-work challenge page (confirmed
   in Anubis's own logs: `"msg":"new challenge issued"`).
3. Byparr's browser never gets past that challenge. Root cause, confirmed
   by reading Anubis's own Set-Cookie headers directly: its
   challenge-verification cookies (`techaro.lol-anubis-cookie-verification-*`)
   are marked `Secure; SameSite=None`, so no spec-compliant browser
   (including the real Chromium Byparr drives) will ever persist them over
   this stack's plain `http://` -- confirmed independently by Anubis's own
   log line for the attempt: `"msg":"user has cookies disabled, this is
   not an anubis bug"`. The challenge cannot complete as this stack is
   currently deployed (HTTP-only), regardless of Byparr itself.
4. The result: ByparrMiddleware returns a 200 response whose body is
   Anubis's *challenge* page, not mock-target's real HTML. GenericSpider's
   selectors find nothing there (correctly -- there is nothing there to
   find), logs `generic_spider.no_items_found`, and the crawl finishes
   cleanly with zero items. No honeypot is ever reached (nothing beyond
   the one blocked GET happens), so security/honeypot_triggers.log stays
   untouched by this run.

This is a genuine environment/deployment gap -- test-environment's mock
stack needs TLS for Anubis's own challenge to ever be completable by any
real browser -- not a bug in GenericSpider, ByparrMiddleware, or Byparr
itself. See docs/REQUIREMENTS.md's "Known Gaps from Test Environment"
section for the tracked, dated entry.

Separately (and independently confirmed with a plain `curl -A
"Scrapy/2.18.0 (+https://scrapy.org)"`, no Scrapy involved at all): Anubis's
real, unmodified default policy also *explicitly denies* Scrapy's own
default User-Agent outright via its `bot/ai-catchall` deny rule (confirmed
in Anubis's logs: `"msg":"explicit deny", "check_result":{"name":"bot/ai-catchall","rule":"DENY"}`)
-- self-identifying crawlers that name themselves in their own User-Agent,
exactly what that deny list is built to catch. So this target is doubly
gated for GenericSpider today: a direct, un-rendered request is denied
outright, and Byparr's browser-driven request is stuck on a challenge it
cannot complete over plain HTTP.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from tests.integration._live_helpers import run_spider_live

BYPARR_URL = os.environ.get("TITAN_BYPARR_URL")
REPO_ROOT = Path(__file__).resolve().parents[2]
HONEYPOT_LOG = REPO_ROOT / "test-environment" / "logs" / "honeypot_triggers.log"


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no Byparr instance running)"
)
def test_mock_target_yields_zero_items_stuck_behind_anubis_challenge(tmp_path: Path) -> None:
    """Documents the real, current outcome (see module docstring for the
    full, evidenced root-cause analysis): the crawl finishes cleanly, but
    with zero items, because Byparr's browser never gets past Anubis's
    proof-of-work challenge over plain HTTP. This assertion is a
    regression sentinel, not an aspiration -- if this stack ever gains
    TLS (or Anubis relaxes the Secure-cookie requirement for a non-TLS
    deployment) and the crawl starts finding real posts, this test should
    be updated to match, the same way ajax-javascript/load-more were
    updated once render_wait_ms/click_selector genuinely fixed them.
    """
    assert BYPARR_URL  # guarded by skipif above; narrows type for mypy too
    output_path = tmp_path / "mock_target_live.jsonl"

    items = run_spider_live(
        "mock_target.yaml", output_path, extra_settings={"TITAN_BYPARR_URL": BYPARR_URL}
    )

    assert items == [], (
        f"expected zero items (Anubis challenge not yet completable over plain "
        f"HTTP -- see this test's module docstring); got {len(items)}: {items[:1]}"
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
        "mock_target.yaml", output_path, extra_settings={"TITAN_BYPARR_URL": BYPARR_URL}
    )

    after_size = HONEYPOT_LOG.stat().st_size if HONEYPOT_LOG.exists() else None
    assert after_size == before_size, (
        "honeypot_triggers.log grew during a crawl that should never have "
        "reached real mock-target content -- see this module's docstring"
    )
