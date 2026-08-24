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

**Revision history for this test's own expected count -- both real, not
hypothetical:**

*Attempt 1, first version (wrong, CI-confirmed failed -- CI run
32730994089): expected 10.* Reasoning at the time: the first
``/api/feed`` batch trims to 5, one more scroll-triggered batch trims to
a second, disjoint 5. **What actually happened:** both extraction modes
got exactly 5, not 10 -- ``camoufox_provider.solved``'s own structured
log line showed ``html_snapshot_count: 2`` (parsed_html) and
``live_dom_item_count: 5`` (live_dom): the collection machinery ran
exactly as designed (2 reads happened), but both reads captured the
*same* 5-post window. Root cause, confirmed from that same evidence, not
guessed: ``scroll_and_collect`` copied ``scroll_to_load_lazy_content``'s
"stop once ``document.body.scrollHeight`` stops growing" heuristic --
invalid for a bounded-window virtualized target, whose rendered height
never meaningfully grows between steps regardless of how much new
content actually loaded, so the loop always gave up after exactly one
scroll attempt. See ``src/providers/antibot/_scroll.py``'s own
"Revision" docstring for the fix (no early exit; always run every
``max_attempts`` cycle; also dispatch a synthetic ``'scroll'`` ``Event``
explicitly, since ``scrollTo()`` alone doesn't reliably fire one once
rendered content is short enough to fit the viewport, which
``templates/feed.html``'s own ``loadMore()`` trigger depends on).

*Attempt 1, revision 2 (this version) -- expected 25, re-derived from
the same code, now accounting for both bugs above being fixed:*
``test-environment/mock-target/structural/feed.py``'s ``MAX_FEED_PAGES``
(5) and ``content_generator.py``'s ``generate_feed_page`` (globally
unique ``{seed}-post-{index}`` ids, no collisions across pages) mean
there are 5 total pages, each 10 posts. Tracing ``templates/feed.html``'s
own trim rule (``container.children.length - windowSize``, oldest-first
eviction) through all 5 loads: page 0 alone trims 10 down to its own
last 5 (ids 5-9). Each subsequent page's 10-post batch lands on top of
the *already-trimmed* 5-post remainder (5 + 10 = 15), and trimming that
down to 5 always evicts the entire old remainder plus that new page's
own first 5 -- leaving only that new page's own *last* 5. So every one
of the 5 pages ever contributes its own last 5 posts to any window a
read can catch, never the full 10 -- 5 pages x 5 posts = 25 unique
``post_id``\\ s total, not 50 and not 10. ``page.has_next_page`` goes
``False`` after page 4 loads (``page < MAX_FEED_PAGES - 1``), so
whichever scroll attempts remain after that just re-observe the same,
final window -- harmless no-ops, already deduplicated by ``post_id``.
``FEED_RATE_LIMIT_THRESHOLD`` (20 requests per 60s) has ample headroom
over the 5 ``/api/feed`` calls one crawl actually makes.

*Attempt 1, revision 2 -- real CI result (CI run 32733064348, second
attempt after an unrelated infra flake on the first): 25 confirmed for
``live_dom``, but ``parsed_html`` got 20, not 25 -- one page's window
short.* ``camoufox_provider.solved``'s own log line showed
``html_snapshot_count: 9`` (all 8 scroll attempts ran, no early exit --
the revision 1 fix held), so this wasn't the same bug again. Root cause:
a genuine async race, not a collection-logic bug --
``templates/feed.html``'s own ``loading`` flag silently drops a
scroll-triggered ``loadMore()`` call if the *previous* one's fetch is
still in flight, and the shared ``DEFAULT_SCROLL_PAUSE_MS`` (700ms,
tuned for a plain lazy-load target's simpler DOM-append cost) doesn't
reliably leave margin for one full fetch+trim round trip under real,
sometimes-loaded CI network conditions -- costing one page's worth of
window when it happens. Fix (revision 3, this version): dedicated,
more generous constants for the progressive path specifically
(``DEFAULT_PROGRESSIVE_MAX_SCROLL_ATTEMPTS`` = 10,
``DEFAULT_PROGRESSIVE_SCROLL_PAUSE_MS`` = 1500ms) in both
``camoufox_provider.py`` and ``patchright_provider.py`` -- the shared
``DEFAULT_MAX_SCROLL_ATTEMPTS``/``DEFAULT_SCROLL_PAUSE_MS`` used by
``scroll_to_load_lazy_content`` (every other already-proven caller) are
untouched. 25 is still the expected, deterministic total -- only the
timing margin around getting there changed.

Requires the same TITAN_BYPARR_URL-gated live-CI signal every other test
in this package uses.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from tests.integration._live_helpers import run_spider_live

BYPARR_URL = os.environ.get("TITAN_BYPARR_URL")

# 5 pages x 5 posts/page (each page's own trimmed-down-to-window_size
# remainder) -- see this module's own docstring for the full, re-derived
# reasoning after attempt 1's first (wrong) prediction of 10.
EXPECTED_TOTAL_POSTS = 25


def _assert_all_posts_recovered_across_every_window(items: list[dict[str, object]]) -> None:
    assert len(items) == EXPECTED_TOTAL_POSTS, (
        f"expected exactly {EXPECTED_TOTAL_POSTS} items (all 5 virtualization windows' "
        f"worth, merged -- see this module's docstring for the full derivation) -- got "
        f"{len(items)}: progressive collection should recover every post that was ever "
        f"rendered, not just whatever survives in the DOM at the single moment a single, "
        f"final read would have happened"
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
def test_progressive_parsed_html_recovers_every_virtualization_window(tmp_path: Path) -> None:
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

    _assert_all_posts_recovered_across_every_window(items)


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no live-network CI stack running)"
)
def test_progressive_live_dom_recovers_every_virtualization_window(tmp_path: Path) -> None:
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

    _assert_all_posts_recovered_across_every_window(items)
