"""Integration test: src/spiders/configs/mock_target_camoufox.yaml against
the real, live test-environment/ stack -- the same target and challenge as
test_mock_target_live.py, this time via `antibot_provider: camoufox`
(docs/REQUIREMENTS.md section 9 entry 4 / round 3's first real test of
SpiderConfig's provider-selection field).

Requires the same TITAN_BYPARR_URL-gated live-CI signal every other test
in this package uses (Camoufox itself needs no external service -- it
drives its own browser in-process -- but the test-environment/ stack
still needs to be up, which the CI workflow brings up alongside Byparr;
skips cleanly, same as every other live test, in a plain local
`pytest tests/unit` run).

**Confirmed for real, not just expected** (CI run
[32507637737](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32507637737),
21/21 tests passed): Camoufox genuinely gets past Anubis's real
proof-of-work challenge and this crawl yields real posts. Getting here
took three real, evidenced fixes across this investigation
(docs/REQUIREMENTS.md section 9, entries 1-5 -- full history, not
summarized, is there):

1. Unlike Byparr (a `services:` container on a different Docker network
   than this stack, entry 1), Camoufox drives its browser in-process, in
   the same network namespace as Scrapy itself -- that class of mismatch
   structurally cannot happen here.
2. `CamoufoxProvider` originally crashed outright ("Playwright Sync API
   inside the asyncio loop") because it was called straight from
   Scrapy's own reactor thread -- fixed by running provider solving via
   `deferToThread`, the same pattern `PlaywrightMiddleware` already used
   for the identical Playwright constraint (entry 5).
3. Anubis's challenge-verification cookies need *both*
   `COOKIE_SECURE=false` and `COOKIE_PARTITIONED=false` to persist in a
   non-TLS deployment -- the second flag was verified by hand in round 2
   but only actually committed to `docker-compose.test.yml` once this
   test kept failing with "user has cookies disabled" even after the
   crash was fixed (entry 5's final update).

Only once all three were real did CamoufoxProvider's actual point --
holding the browser open a configurable `post_load_wait_ms` (default 5s)
past the page's `load` event -- get to matter: that's what gives
Anubis's real, asynchronous post-load proof-of-work flow (unlike
Byparr's `/v1` API, which tears the browser down right at `load`,
entry 4) an actual chance to finish.

**Since then, a second, independent gate was stacked behind Anubis**
(docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md's cookie-consent-wall
round, docs/REQUIREMENTS.md section 9 entry 8): test-environment's
mock-target now hides real content behind a real, server-side consent
wall until an "Accept" link is followed. `mock_target_camoufox.yaml`
gained `click_selector: "#accept-cookies"` for it, and
`CamoufoxProvider.solve()` gained real click support for the same
reason `PlaywrightMiddleware`'s renderer already had it (a real browser
can click; `ByparrProvider`'s external API structurally cannot).

**Confirmed for real again, not assumed from round 3's own success**
(CI run
[32528886186](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32528886186),
23/23 tests passed): this crawl still yields real posts with the cookie
wall active -- Camoufox clicks `#accept-cookies` for real, then
`post_load_wait_ms` gives the resulting redirect time to settle before
reading content, the same way it already gave Anubis's own async
challenge JS time to finish. A different browser engine or stealth layer
(entry 7's Patchright) getting past Anubis is not assumed to mean it
also gets past this second gate, and vice versa -- each is verified
independently.

**Integration round (docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md's own
"العقبات المركّبة" framing, first half):** every test above already ran
with `ENABLE_HONEYPOTS`, `ENABLE_MARKUP_RANDOMIZER`, `ENABLE_COOKIE_WALL`,
and `ENABLE_AB_VARIANTS` all simultaneously on (`docker-compose.test.yml`'s
defaults were never turned off between rounds, only added to) -- but
nothing ever asserted on more than the aggregate outcome (items > 0) of
that combination. This is a **دمج قديم** round (a deliberate, explicit
integration test of four already-individually-proven layers -- honeypots
and markup randomization from Round 1, the cookie wall and A/B variants
from the rounds documented above), not a new-difficulty round: no new
layer is added here. Camoufox is the only provider for which this is a
meaningful check at all -- Byparr and Patchright's own honeypot-avoidance
tests (`test_mock_target_live.py`,
`test_mock_target_patchright_live.py`) are trivially true (they never get
past Anubis, so they never reach real content -- let alone a honeypot --
in the first place). Camoufox's crawl below is the one real case where
all four layers are genuinely live *and* the crawl genuinely reaches real
content past all of them, so this is the first real, evidenced proof they
hold up together, not just individually.
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
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no live-network CI stack running)"
)
def test_mock_target_camoufox_gets_past_anubis_and_yields_real_posts(tmp_path: Path) -> None:
    output_path = tmp_path / "mock_target_camoufox_live.jsonl"

    items = run_spider_live("mock_target_camoufox.yaml", output_path, timeout=180)

    assert len(items) > 0, (
        "expected real posts -- Camoufox is confirmed to get past Anubis's "
        "challenge (see this test's module docstring for the real CI evidence); "
        "got 0 items -- something regressed"
    )
    first = items[0]
    assert first["post_id"], "post_id should not be empty"
    assert first["author"], "author should not be empty"


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no live-network CI stack running)"
)
def test_mock_target_camoufox_crawl_gets_real_posts_and_never_reaches_a_real_honeypot(
    tmp_path: Path,
) -> None:
    """The combined-round check (see this module's docstring): unlike
    `test_mock_target_live.py`'s / `test_mock_target_patchright_live.py`'s
    identically-shaped honeypot checks, this crawl genuinely gets past
    Anubis *and* the cookie wall *and* whichever A/B variant/markup-
    randomizer state this particular request landed on -- so a honeypot
    hit here would be real, evidenced proof `GenericSpider`'s
    `data-role="post"` selector reached into the four hidden trap links
    `structural/honeypots.py` renders on the very same page, not just an
    absence of opportunity to. It shouldn't: the selector only ever
    targets `[data-role="post"]` elements, never the honeypots' own
    `<a>` tags, regardless of which A/B container tag or randomized class
    is active on this request.
    """
    before_size = HONEYPOT_LOG.stat().st_size if HONEYPOT_LOG.exists() else None

    output_path = tmp_path / "mock_target_camoufox_live_honeypot_check.jsonl"
    items = run_spider_live("mock_target_camoufox.yaml", output_path, timeout=180)

    # Real access is part of what this test is actually proving (see the
    # module docstring) -- a crawl that silently regressed to 0 items
    # would make the honeypot assertion below vacuous, the same way it
    # would for the byparr/patchright sentinels' *own* honeypot checks if
    # they ever stopped being stuck at Anubis for a real reason.
    assert len(items) > 0, (
        "expected real posts (same as this file's other test) -- got 0 items, "
        "which would make the honeypot check below meaningless (never got a "
        "real chance to reach one either way)"
    )

    after_size = HONEYPOT_LOG.stat().st_size if HONEYPOT_LOG.exists() else None
    assert after_size == before_size, (
        "honeypot_triggers.log grew during a crawl that reached real "
        "mock-target content past Anubis, the cookie wall, A/B variants, "
        "and markup randomization -- see this module's docstring"
    )
