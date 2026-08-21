"""Integration test: webscraper.io/test-sites/pagination (Level 3,
docs/TEST_TARGETS.md).

Static HTML classic-cars catalog, numbered pagination via a
"a.page-link.next" link. Capped to 2 pages via CLOSESPIDER_PAGECOUNT.
"""

from __future__ import annotations

from pathlib import Path

from tests.integration._live_helpers import run_spider_live

# Confirmed by investigation: 6 cars per page, 17 pages total.
ITEMS_PER_PAGE = 6


def test_webscraper_io_pagination_advances_past_page_one(tmp_path: Path) -> None:
    output_path = tmp_path / "webscraper_io_pagination_live.jsonl"

    items = run_spider_live(
        "webscraper_io_pagination.yaml",
        output_path,
        extra_settings={"CLOSESPIDER_PAGECOUNT": "2"},
    )

    assert len(items) > ITEMS_PER_PAGE, (
        f"expected more than {ITEMS_PER_PAGE} cars (one page) after following the 'next' "
        f"pagination link; got {len(items)}"
    )
    first = items[0]
    assert first["name"], "car name should not be empty"
    assert first["price"], "car price should not be empty"
