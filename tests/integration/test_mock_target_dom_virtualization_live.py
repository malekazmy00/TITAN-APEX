"""Integration test: src/spiders/configs/mock_target_feed_virtualized_parsed_html.yaml
and mock_target_feed_virtualized_live_dom.yaml against the real, live
test-environment/ stack -- docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md's
DOM Virtualization round (محور 3), docs/REQUIREMENTS.md section 9 entry
13.

**The real, open question this pair of tests answers:** entry 12 showed
`extraction_mode: live_dom` recovers content Shadow DOM merely
*encapsulates* from a string-based parser. Does the same trick recover
content a virtualized list has *evicted* -- genuinely,
`container.removeChild`-style removed from the DOM, not hidden or
encapsulated anywhere -- by the time either extraction strategy actually
reads the page?

**Prerequisite gap found and fixed on the way here, not part of the
virtualization question itself:** neither `CamoufoxProvider` nor
`PatchrightProvider` had *any* scroll capability before this round --
`PlaywrightMiddleware`'s own scroll-to-stable loop
(`render_with_playwright`) is structurally unreachable for any
Anubis-protected target, since `ByparrMiddleware` already returns a
response first and Scrapy stops walking the downloader-middleware chain
once one does. `/feed`'s own lazy-loading content
(test-environment/README.md section 2.4) was therefore never actually
reachable via any of this project's three `AntibotProvider`s at all
until `src.providers.antibot._scroll.scroll_to_load_lazy_content` was
added and wired into both real-browser providers' solve functions.

Requires the same TITAN_BYPARR_URL-gated live-CI signal every other test
in this package uses.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from tests.integration._live_helpers import run_spider_live

BYPARR_URL = os.environ.get("TITAN_BYPARR_URL")

# structural/dom_virtualization.py's own default (docker-compose.test.yml's
# DOM_VIRTUALIZATION_WINDOW_SIZE) -- deterministic, not approximate:
# /api/feed's own FEED_PAGE_SIZE (10) always exceeds this window, so the
# very first loaded batch already triggers eviction down to exactly this
# many posts, and every later batch keeps it there -- regardless of how
# many scroll-triggered loads the provider's own scroll loop actually
# manages to fire in a given CI run.
EXPECTED_WINDOW_SIZE = 5


def _assert_only_the_final_window_survives(items: list[dict[str, object]]) -> None:
    assert len(items) == EXPECTED_WINDOW_SIZE, (
        f"expected exactly {EXPECTED_WINDOW_SIZE} items (structural/dom_virtualization.py's "
        f"own window size -- deterministic, see this module's docstring for why) -- got "
        f"{len(items)}: a genuinely evicted post should never be recoverable by either "
        f"extraction strategy, since it no longer exists in the DOM by read time"
    )
    for item in items:
        assert item["post_id"], f"post_id should not be empty: {item}"
        assert item["author"], f"author should not be empty: {item}"
    post_ids = [item["post_id"] for item in items]
    assert len(post_ids) == len(set(post_ids)), (
        f"expected every surviving post_id to be unique (no decoy-twin-shaped "
        f"duplicate exists on /feed, unlike / -- entry 12's own test) -- got {post_ids}"
    )


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no live-network CI stack running)"
)
def test_parsed_html_only_recovers_the_final_virtualization_window(tmp_path: Path) -> None:
    """Regression sentinel, not an aspiration -- documents the real,
    confirmed outcome for the `extraction_mode: parsed_html` (default)
    path: `page.content()` is read once, after the provider's own scroll
    loop finishes, so it can only ever reflect whatever's in the DOM at
    that single moment -- the final window, never anything evicted along
    the way."""
    output_path = tmp_path / "mock_target_feed_virtualized_parsed_html_live.jsonl"

    items = run_spider_live(
        "mock_target_feed_virtualized_parsed_html.yaml", output_path, timeout=180
    )

    _assert_only_the_final_window_survives(items)


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no live-network CI stack running)"
)
def test_live_dom_also_only_recovers_the_final_virtualization_window(tmp_path: Path) -> None:
    """Same regression-sentinel shape as the parsed_html test above, for
    `extraction_mode: live_dom` -- unlike entry 12's Shadow DOM fix,
    piercing into the live DOM doesn't help here: `page.locator()` also
    only ever queries the DOM's *current* state, at the single moment
    it's called (after the same scroll loop finishes) -- an evicted post
    isn't merely unreachable by a selector, it genuinely isn't there to
    find. Both extraction modes are expected to fail identically, for the
    same reason -- a timing + DOM-existence problem, not a
    selector-reachability one (Shadow DOM's own shape)."""
    output_path = tmp_path / "mock_target_feed_virtualized_live_dom_live.jsonl"

    items = run_spider_live("mock_target_feed_virtualized_live_dom.yaml", output_path, timeout=180)

    _assert_only_the_final_window_survives(items)
