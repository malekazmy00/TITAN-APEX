"""Integration test:
src/spiders/configs/mock_target_interstitial_camoufox_unhandled.yaml,
mock_target_interstitial_camoufox_dismissed.yaml, and
mock_target_interstitial_patchright_dismissed.yaml against the real,
live test-environment/ stack -- docs/REQUIREMENTS.md section 9 entry 16
(Interstitials, محور 6, activated after Login/Session per explicit user
request).

**The real, open question these tests answer:** does the existing
click_selector mechanism (already proven for the cookie-consent wall)
generalize to a full-screen interstitial that appears *after* the page
has already loaded (time-triggered here, not gating the very first
response the way the cookie wall does) and genuinely blocks further
content loading -- not just the view -- until dismissed? Run first,
deliberately unmodified (no click_selector) against
mock_target_interstitial_camoufox_unhandled.yaml, to confirm the real
obstacle before reaching for the fix (docs/REQUIREMENTS.md section 8's
own Escalation Cycle): the interstitial appears
(INTERSTITIAL_DELAY_MS=1000) well before this provider's own
post_load_wait_ms (5000 default) ever lets scrolling begin, so only the
one, automatic first batch is ever captured -- not zero (the page isn't
broken), not everything either.

``/`` and ``/feed`` themselves are left completely unchanged by this
round; ``/feed-interstitial`` is a deliberately isolated route with its
own content generator (test-environment/mock-target/structural/interstitial.py's
``build_interstitial_feed_page``), sharing no client-side JS or server
state with ``/feed``'s own DOM-virtualization/rate-limiting machinery.

Requires the same TITAN_BYPARR_URL-gated live-CI signal every other test
in this package uses.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from tests.integration._live_helpers import run_spider_live

BYPARR_URL = os.environ.get("TITAN_BYPARR_URL")

# mock-target/config.py's own defaults (INTERSTITIAL_FEED_PAGE_SIZE,
# INTERSTITIAL_FEED_TOTAL_BATCHES).
EXPECTED_FIRST_BATCH_SIZE = 5
EXPECTED_TOTAL_ITEMS = 5 * 3


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no live-network CI stack running)"
)
def test_unhandled_interstitial_blocks_further_loading_after_the_first_batch(
    tmp_path: Path,
) -> None:
    """The "before" half: no click_selector configured, the real
    obstacle this round is actually about -- the overlay shows up
    before any scroll attempt happens and the page's own JS refuses to
    fetch another batch while it's up (structural/interstitial.py's own
    docstring on why this is a JS flag, not CSS overflow). Real content
    already on the page (the first batch) stays genuinely present and is
    still captured -- this is not the crawl failing, it's a real,
    partial obstacle."""
    output_path = tmp_path / "mock_target_interstitial_camoufox_unhandled_live.jsonl"

    items = run_spider_live(
        "mock_target_interstitial_camoufox_unhandled.yaml", output_path, timeout=180
    )

    assert len(items) == EXPECTED_FIRST_BATCH_SIZE, (
        f"expected exactly {EXPECTED_FIRST_BATCH_SIZE} items -- the interstitial appears "
        f"before any scroll attempt and blocks every batch after the first automatic one -- "
        f"got {len(items)}"
    )
    post_ids = [item["post_id"] for item in items]
    assert len(post_ids) == len(set(post_ids)), f"expected unique post_ids -- got {post_ids}"


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no live-network CI stack running)"
)
def test_camoufox_dismisses_the_interstitial_and_yields_every_batch(tmp_path: Path) -> None:
    """The "after" half: the same real obstacle, this time with
    click_selector set -- the same fix the cookie-consent wall already
    proved, not a new mechanism (docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md's
    own point). Playwright's actionability checks make the click wait
    for the close button to actually become visible first, so it
    reliably dismisses the overlay before any scroll attempt -- every
    batch loads normally."""
    output_path = tmp_path / "mock_target_interstitial_camoufox_dismissed_live.jsonl"

    items = run_spider_live(
        "mock_target_interstitial_camoufox_dismissed.yaml", output_path, timeout=180
    )

    assert len(items) == EXPECTED_TOTAL_ITEMS, (
        f"expected exactly {EXPECTED_TOTAL_ITEMS} items -- click_selector dismisses the "
        f"interstitial before any scroll attempt, so nothing should be blocked -- "
        f"got {len(items)}"
    )
    post_ids = [item["post_id"] for item in items]
    assert len(post_ids) == len(set(post_ids)), f"expected unique post_ids -- got {post_ids}"


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no live-network CI stack running)"
)
def test_patchright_dismisses_the_interstitial_and_yields_every_batch(tmp_path: Path) -> None:
    """Same fix, the other real-browser provider -- confirms the
    click-before-scroll dismissal isn't Camoufox-specific."""
    output_path = tmp_path / "mock_target_interstitial_patchright_dismissed_live.jsonl"

    items = run_spider_live(
        "mock_target_interstitial_patchright_dismissed.yaml", output_path, timeout=180
    )

    assert len(items) == EXPECTED_TOTAL_ITEMS, (
        f"expected exactly {EXPECTED_TOTAL_ITEMS} items -- got {len(items)}"
    )
    post_ids = [item["post_id"] for item in items]
    assert len(post_ids) == len(set(post_ids)), f"expected unique post_ids -- got {post_ids}"
