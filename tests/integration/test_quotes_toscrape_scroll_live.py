"""Integration test: quotes.toscrape.com/scroll (Level 2,
docs/TEST_TARGETS.md).

True infinite-scroll variant -- the static HTML ships an empty
"div.quotes" container, populated progressively by JS as the page
scrolls. Same shape as test_playwright_live_render.py's
scrapingcourse_infinite_scrolling assertion: prove PlaywrightMiddleware's
scroll-to-bottom loop pulls in more than a single batch.
"""

from __future__ import annotations

from pathlib import Path

from tests.integration._live_helpers import run_spider_live

# A single fetchQuotes() call on this site returns 10 quotes; scrolling
# must trigger at least a second batch.
FIRST_BATCH_SIZE = 10


def test_quotes_toscrape_scroll_yields_more_than_the_first_batch(tmp_path: Path) -> None:
    output_path = tmp_path / "quotes_toscrape_scroll_live.jsonl"

    items = run_spider_live("quotes_toscrape_scroll.yaml", output_path)

    assert len(items) > FIRST_BATCH_SIZE, (
        f"expected more than {FIRST_BATCH_SIZE} quotes (the first scroll batch) after "
        f"PlaywrightMiddleware's scroll loop; got {len(items)}"
    )
    first = items[0]
    assert first["text"], "quote text should not be empty"
