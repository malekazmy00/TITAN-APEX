"""Integration test: src/spiders/configs/mock_target_camoufox.yaml against
the real, live test-environment/ stack -- documents a new, genuine Known
Limitation (docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md's Shadow DOM
round, محور 3 "البنية الهيكلية").

Unlike every other structural challenge stacked onto `/` so far (Anubis,
the cookie wall, A/B variants, honeypots, markup randomization --
docs/REQUIREMENTS.md section 9 entry 10's combined-round proof that
Camoufox gets past all four together), this one is a real, confirmed
FAILURE, not another success: `test-environment/mock-target/structural/shadow_dom.py`
renders every other post (odd 0-based index) inside a genuine,
client-side-attached shadow root instead of plain light-DOM markup. Per
the DOM spec, a shadow root attached via `Element.attachShadow()` is
never included when serializing its host element's `outerHTML` --
Playwright's `page.content()` (what `CamoufoxProvider`/`PatchrightProvider`
both return) is exactly `document.documentElement.outerHTML` under the
hood, so it inherits that same blind spot. `GenericSpider.parse()` then
runs a plain Scrapy/parsel CSS selector over that *string* -- there is no
live DOM to pierce, so this content is structurally invisible to it, even
though Camoufox already drives a real, full browser past every other
layer active on the exact same page.

This is the first structural challenge in this project where "drive a
real browser" alone is *not* enough -- the gap is architectural
(GenericSpider never queries a live DOM at all, only ever parses a
serialized string), not a timing/stealth/click gap
`post_load_wait_ms`/`click_selector` could ever close. Fixing this for
real would mean GenericSpider gaining a genuine shadow-DOM-piercing
extraction path (e.g. Playwright locators querying the live page instead
of parsel on `page.content()`) -- a real architectural change, not
attempted here; this test's job is to document the gap with precise,
reproducible evidence, the same as `test_mock_target_live.py`'s and
`test_mock_target_patchright_live.py`'s own confirmed Anubis gaps.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from tests.integration._live_helpers import run_spider_live

BYPARR_URL = os.environ.get("TITAN_BYPARR_URL")


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no live-network CI stack running)"
)
def test_mock_target_camoufox_misses_every_shadow_dom_wrapped_post(tmp_path: Path) -> None:
    """Regression sentinel, not an aspiration -- documents the real,
    current outcome (see module docstring for the full, evidenced
    root-cause). `app.py`'s `INDEX_PAGE_SIZE` (10) and
    `structural/shadow_dom.py`'s `is_shadow_wrapped` (every odd 0-based
    index) are both deterministic, so this crawl's exact item count is a
    precise, reproducible number, not just "fewer than before": 5 real
    posts (even index) stay in light DOM + 1 decoy twin
    (`structural/decoy_data.py`, always light DOM, unaffected by this
    layer) = 6 -- never the 10 real posts `/` actually generates. The
    other 5 (odd index) exist only inside a real shadow root, invisible
    to GenericSpider's string-based CSS selector.

    If this ever changes (GenericSpider gains real shadow-DOM-piercing
    extraction), this test should be updated to match, the same way
    `ajax_javascript`/`load_more` were once `render_wait_ms`/
    `click_selector` genuinely fixed them.
    """
    output_path = tmp_path / "mock_target_shadow_dom_live.jsonl"

    items = run_spider_live("mock_target_camoufox.yaml", output_path, timeout=180)

    # Real access must still hold -- proves this is a genuine, isolated
    # Shadow DOM gap, not just Anubis/the cookie wall failing again.
    assert len(items) > 0, (
        "expected real access to still work (Camoufox already gets past "
        "Anubis/the cookie wall/A/B variants/honeypots, entry 10) -- got 0 "
        "items, which would mean something upstream of Shadow DOM regressed"
    )
    assert len(items) == 6, (
        f"expected exactly 6 items (5 light-DOM real posts + 1 decoy twin) "
        f"-- got {len(items)}: a plain-string CSS selector should never "
        f"reach a Shadow-DOM-wrapped post; see this module's docstring for "
        f"why 6 is the real, deterministic expectation, not an approximation"
    )
