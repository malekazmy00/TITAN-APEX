"""Integration test:
src/spiders/configs/mock_target_feed_virtualized_progressive_parsed_html.yaml
and mock_target_feed_virtualized_progressive_live_dom.yaml against the
real, live test-environment/ stack -- docs/REQUIREMENTS.md section 9
entry 14, the real fix attempt for entry 13's confirmed DOM
Virtualization gap
(tests/integration/test_mock_target_dom_virtualization_live.py, kept
UNCHANGED as a regression sentinel: both extraction modes there still
only recover 5/10 items on the old, single-shot-read path).

**The real, open question this pair of tests answers:** does collecting
a snapshot (or re-querying the live DOM) after *every* scroll step --
before the next step's eviction removes what's rendered right now --
instead of reading the DOM only once after scrolling finishes, actually
recover the posts entry 13 proved were unrecoverable? Not assumed either
way -- see docs/REQUIREMENTS.md for the real, CI-confirmed result.

**Why 10, not 5 -- a real prediction from reading the actual code paths
involved, not a round number:**
``test-environment/mock-target/config.py``'s ``FEED_PAGE_SIZE`` (10) and
``DOM_VIRTUALIZATION_WINDOW_SIZE`` (5) mean the *first* ``/api/feed``
batch (10 posts) is immediately trimmed down to its own last 5 before
``CamoufoxProvider``'s ``post_load_wait_ms`` (5s, run well before any
scrolling starts) even elapses -- so the very first (pre-scroll)
snapshot ``scroll_and_collect`` captures already only has 5 posts in it,
same as entry 13's own single read would have. The one scroll step that
follows triggers exactly one more ``/api/feed`` batch (another 10
posts, trimmed to its own last 5) -- and because a virtualized window's
rendered size stays roughly constant once trimming has kicked in even
once, ``document.body.scrollHeight`` stops growing after that one step,
so ``scroll_to_load_lazy_content``'s own growth check ends the loop
there (identical reasoning to entry 13's own "deterministic regardless
of scroll-cycle count" argument for why exactly 5 survive on the old
path). Two disjoint 5-post windows, captured before either evicts the
other -- 10 unique ``post_id``\\ s, not 5.

Requires the same TITAN_BYPARR_URL-gated live-CI signal every other test
in this package uses.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from tests.integration._live_helpers import run_spider_live

BYPARR_URL = os.environ.get("TITAN_BYPARR_URL")

# Two disjoint DOM_VIRTUALIZATION_WINDOW_SIZE (5) windows -- see this
# module's own docstring for why exactly two snapshots, not more or
# fewer, are expected to be captured before scrolling stabilizes.
EXPECTED_TOTAL_POSTS = 10


def _assert_all_posts_recovered_across_both_windows(items: list[dict[str, object]]) -> None:
    assert len(items) == EXPECTED_TOTAL_POSTS, (
        f"expected exactly {EXPECTED_TOTAL_POSTS} items (both virtualization windows' "
        f"worth, merged -- see this module's docstring for why exactly two, disjoint "
        f"5-post windows are expected) -- got {len(items)}: progressive collection should "
        f"recover every post that was ever rendered, not just whatever survives in the "
        f"DOM at the single moment a single, final read would have happened"
    )
    for item in items:
        assert item["post_id"], f"post_id should not be empty: {item}"
        assert item["author"], f"author should not be empty: {item}"
    post_ids = [item["post_id"] for item in items]
    assert len(post_ids) == len(set(post_ids)), (
        f"expected every recovered post_id to be unique -- progressive collection's own "
        f"post_id-keyed dedup (spanning the *entire* crawl, not just one snapshot) should "
        f"never let a post seen more than once (e.g. still visible on a later read) appear "
        f"twice -- got {post_ids}"
    )


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no live-network CI stack running)"
)
def test_progressive_parsed_html_recovers_both_virtualization_windows(tmp_path: Path) -> None:
    """The real fix, ``extraction_mode: parsed_html`` half: every
    ``page.content()`` snapshot ``collect_html_snapshots`` captures (one
    per scroll step, before the next step's eviction) is parsed and
    merged by ``generic_spider.py`` itself
    (``extract_parsed_html_items`` + a ``post_id``-keyed dict spanning
    every snapshot), recovering posts a single, final read structurally
    cannot."""
    output_path = tmp_path / "mock_target_feed_virtualized_progressive_parsed_html_live.jsonl"

    items = run_spider_live(
        "mock_target_feed_virtualized_progressive_parsed_html.yaml", output_path, timeout=180
    )

    _assert_all_posts_recovered_across_both_windows(items)


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no live-network CI stack running)"
)
def test_progressive_live_dom_recovers_both_virtualization_windows(tmp_path: Path) -> None:
    """The real fix, ``extraction_mode: live_dom`` half:
    ``collect_live_dom_items_progressively`` re-queries the live DOM
    after every scroll step (not just once, at the end) and merges
    results into a ``post_id``-keyed dict spanning the entire crawl --
    unlike entry 13's plain ``live_dom`` config, which only ever reads
    the DOM's state at one single moment, same structural limitation as
    the parsed_html path for this particular (timing, not encapsulation)
    problem."""
    output_path = tmp_path / "mock_target_feed_virtualized_progressive_live_dom_live.jsonl"

    items = run_spider_live(
        "mock_target_feed_virtualized_progressive_live_dom.yaml", output_path, timeout=180
    )

    _assert_all_posts_recovered_across_both_windows(items)
