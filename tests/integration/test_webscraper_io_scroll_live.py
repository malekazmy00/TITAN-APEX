"""Integration test: webscraper.io/test-sites/scroll (Tier 2,
docs/TEST_TARGETS.md).

True infinite scroll -- 6 static cards plus a
"<div class='scroll-more' data-next-page='2'>" marker at the bottom.
Proves PlaywrightMiddleware's scroll-to-bottom loop pulls in more than
the first batch, same shape as test_quotes_toscrape_scroll_live.py.
"""

from __future__ import annotations

from pathlib import Path

from tests.integration._live_helpers import run_spider_live

# Confirmed by investigation: 6 cars in the first static batch.
FIRST_BATCH_SIZE = 6


def test_webscraper_io_scroll_yields_more_than_the_first_batch(tmp_path: Path) -> None:
    output_path = tmp_path / "webscraper_io_scroll_live.jsonl"

    items = run_spider_live("webscraper_io_scroll.yaml", output_path)

    assert len(items) > FIRST_BATCH_SIZE, (
        f"expected more than {FIRST_BATCH_SIZE} cars (the first batch) after "
        f"PlaywrightMiddleware's scroll loop; got {len(items)}"
    )
    first = items[0]
    assert first["name"], "car name should not be empty"
