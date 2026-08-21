"""Integration test: webscraper.io/test-sites/load-more (Tier 2,
docs/TEST_TARGETS.md).

Second attempt at this target: without click_selector, real CI (run
32471707326) confirmed the "Load More" button is genuinely
click-triggered, not scroll-triggered -- item count stayed at exactly 6
(the static first batch) every time.

webscraper_io_load_more.yaml now sets click_selector: "button.load-more-btn",
a config-only instruction PlaywrightMiddleware uses to click the button
after navigation, before reading page.content() -- see
docs/REQUIREMENTS.md section 7 entry 4 for the full history. This test
is what proves whether that actually closes the gap for real.
"""

from __future__ import annotations

from pathlib import Path

from tests.integration._live_helpers import run_spider_live

# Confirmed by investigation: 6 cars in the first static batch.
FIRST_BATCH_SIZE = 6


def test_webscraper_io_load_more_yields_more_than_the_first_batch(tmp_path: Path) -> None:
    output_path = tmp_path / "webscraper_io_load_more_live.jsonl"

    items = run_spider_live("webscraper_io_load_more.yaml", output_path)

    assert len(items) > FIRST_BATCH_SIZE, (
        f"expected more than {FIRST_BATCH_SIZE} cars (the first batch) after "
        f"click_selector clicked 'Load More'; got {len(items)}"
    )
    first = items[0]
    assert first["name"], "car name should not be empty"
