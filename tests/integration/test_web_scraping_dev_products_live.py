"""Integration test: web-scraping.dev/products (Level 3,
docs/ADVANCED_TEST_TARGETS_L3.md) -- the main catalog target on
web-scraping.dev, "designed to match real production issues" (the
site's own description), not a simplified exercise.

**Real, evidenced pagination gap found while writing this config, not
guessed:** the site's own pagination widget only ever shows a sliding
window of page-number links, and the trailing "next" arrow has *no*
href at all once that window's last page is reached -- confirmed by
hand (curl) on page 5 of 6: the widget shows links 1-5 and a
href-less ">" arrow, even though page 6 (with 3 more products, 28
total across 6 pages per the page's own "page 5 of total 28 results in
6 pages" text) genuinely exists at .../products?page=6. Following only
"next" links -- what next_page (GenericSpider's own, deliberately
simple mechanism) does, the same way a naive link-following bot would
-- therefore reaches 25 of 28 products, not all of them. This is a
real site behavior, not a code bug: recorded here as a known,
real-world-shaped limitation of next-link-only pagination, not solved
in this same round (a numeric ?page=N construction strategy would be a
genuinely new, separate capability).
"""

from __future__ import annotations

from pathlib import Path

from tests.integration._live_helpers import run_spider_live


def test_web_scraping_dev_products_reaches_the_next_link_window(tmp_path: Path) -> None:
    output_path = tmp_path / "web_scraping_dev_products_live.jsonl"

    items = run_spider_live("web_scraping_dev_products.yaml", output_path)

    assert len(items) == 25, (
        f"expected exactly 25 products (5 pages of 5, the real next-link window this "
        f"site's own pagination widget exposes -- see this test's module docstring) -- "
        f"got {len(items)}"
    )
    first = items[0]
    assert first["title"], "title should not be empty"
    assert first["price"], "price should not be empty"
    assert first["product_url"].startswith("https://web-scraping.dev/product/")
