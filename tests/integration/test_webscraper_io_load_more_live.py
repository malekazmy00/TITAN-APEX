"""Integration test: webscraper.io/test-sites/load-more (Tier 2,
docs/TEST_TARGETS.md).

Unlike webscraper_io_scroll.yaml's explicit scroll marker, this page's
extra content sits behind a "Load More" *button*
(class="load-more-btn ecommerce-items-scroll-more"), and
PlaywrightMiddleware only scrolls -- it has no click support. This is
run for real against the live site rather than assumed to fail (the
button's own class name suggests it might also be scroll-wired): the
real, evidenced CI result decides whether this is a working scroll
target or a documented "needs click support" gap -- see the Test
Targets report and docs/REQUIREMENTS.md sections 6/7.
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
        f"PlaywrightMiddleware's scroll loop -- got {len(items)}. If this is exactly "
        f"{FIRST_BATCH_SIZE}, the 'Load More' button genuinely needs a click, not a scroll: "
        f"a real, evidenced gap, not an assumption -- see docs/REQUIREMENTS.md section 7"
    )
    first = items[0]
    assert first["name"], "car name should not be empty"
