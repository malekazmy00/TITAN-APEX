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
succeed on the first try). **The authoritative mechanism is the one
confirmed in real CI** (run 32479883962), not the one first found in local
manual testing -- the two environments turned out to differ in a way that
mattered, and CI is what this project's own rules treat as ground truth:

1. GenericSpider issues one plain GET, meta={"antibot_needed": True}, so
   ByparrMiddleware (not the raw downloader) handles it, calling Byparr's
   `/v1` API with `url=http://localhost:8080/` (Anubis's published port).
2. **In real CI, this fails at the network level, before Anubis is even
   involved.** Byparr runs as a `services:` container -- its own separate
   Docker container, on its own separate network, entirely apart from
   `docker-compose.test.yml`'s `test-environment`/`edge` networks. From
   *inside Byparr's own container*, `localhost` means Byparr's own
   container, not the GitHub Actions runner host -- so its browser gets
   `NS_ERROR_CONNECTION_REFUSED` trying to reach `localhost:8080`,
   confirmed directly in Byparr's own service-container log for this exact
   run: `"Page.goto: NS_ERROR_CONNECTION_REFUSED"` for
   `http://localhost:8080/`, twice (once per test in this file).
   `ByparrProvider.solve()` raises `AntibotError`, `ByparrMiddleware` logs
   `byparr_middleware.solve_failed_fallback` and falls back to a plain
   Scrapy download instead -- which *does* reach Anubis for real, since
   Scrapy itself runs directly on the runner (not in a container) and the
   runner's own `localhost:8080` is where `docker compose`'s port
   publishing actually lands.
3. That plain, un-rendered Scrapy request is what Anubis's real,
   unmodified default policy (`anubis/botPolicy.yaml`) actually evaluates
   -- and it explicitly **denies** Scrapy's own default User-Agent
   (`Scrapy/2.18.0 (+https://scrapy.org)`) outright via the shipped
   `bot/ai-catchall` deny rule (independently reproduced with a plain
   `curl -A "Scrapy/2.18.0 (+https://scrapy.org)"`, no Scrapy involved at
   all: `"msg":"explicit deny","check_result":{"name":"bot/ai-catchall","rule":"DENY"}`
   in Anubis's own log) -- a well-behaved, self-identifying crawler is
   exactly what that deny list targets.
4. The result: GenericSpider's selectors find nothing on Anubis's deny
   response (correctly -- there is nothing there to find), logs
   `generic_spider.no_items_found`, and the crawl finishes cleanly with
   zero items. No honeypot is ever reached (nothing beyond the one denied
   GET happens), so security/honeypot_triggers.log stays untouched.

This is a genuine environment/deployment gap in how this test wires Byparr
(a `services:` container) to a separately-networked `docker compose`
stack -- not a bug in GenericSpider or ByparrMiddleware. Fixing it for
real would mean attaching Byparr to the same Docker network as
`test-environment/docker-compose.test.yml` (not possible for a plain
`services:` entry -- it would need its own `docker compose` step, same
shape as `test-environment` itself) or giving it a host-reachable address
instead of `localhost`.

**A second, independent gap was found in local testing** (Byparr and
Anubis sharing one Docker network, addressed by container name, so Byparr
really did reach Anubis and receive an actual proof-of-work challenge --
weight `+10` for its browser-like User-Agent, Anubis's own log:
`"msg":"new challenge issued"`): even with that network gap fixed, Byparr's
browser still could not *complete* the challenge, because Anubis's
challenge-verification cookies are marked `Secure`, and no spec-compliant
browser (including Byparr's real Chromium) persists a `Secure` cookie over
this stack's plain `http://` (confirmed independently by Anubis's own log
line: `"msg":"user has cookies disabled, this is not an anubis bug"`).
So closing the network gap alone would not be enough -- the stack would
also need TLS. Both gaps are tracked in docs/REQUIREMENTS.md's "Known Gaps
from Test Environment" section.
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
    """Documents the real, current CI outcome (see module docstring for the
    full, evidenced root-cause analysis): the crawl finishes cleanly, but
    with zero items -- in CI, because Byparr's `services:` container can't
    reach `localhost:8080` at all (a network-namespace mismatch, not
    Anubis), so the request falls back to a plain Scrapy GET that Anubis's
    real policy denies outright. This assertion is a regression sentinel,
    not an aspiration -- if this ever changes (Byparr wired onto the same
    network as this stack, and the stack gains TLS for Anubis's
    Secure-cookie challenge to be completable too) and the crawl starts
    finding real posts, this test should be updated to match, the same way
    ajax-javascript/load-more were updated once render_wait_ms/click_selector
    genuinely fixed them.
    """
    assert BYPARR_URL  # guarded by skipif above; narrows type for mypy too
    output_path = tmp_path / "mock_target_live.jsonl"

    items = run_spider_live(
        "mock_target.yaml", output_path, extra_settings={"TITAN_BYPARR_URL": BYPARR_URL}
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
        "mock_target.yaml", output_path, extra_settings={"TITAN_BYPARR_URL": BYPARR_URL}
    )

    after_size = HONEYPOT_LOG.stat().st_size if HONEYPOT_LOG.exists() else None
    assert after_size == before_size, (
        "honeypot_triggers.log grew during a crawl that should never have "
        "reached real mock-target content -- see this module's docstring"
    )
