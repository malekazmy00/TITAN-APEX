"""Integration test: scrapingcourse.com/pagination (Level 3,
docs/TEST_TARGETS.md).

Static HTML, numbered pagination via a "Next page" link
(a.next-page / rel=next). Capped to 2 pages via CLOSESPIDER_PAGECOUNT.
"""

from __future__ import annotations

from pathlib import Path

from tests.integration._live_helpers import run_spider_live

# Confirmed by investigation: 12 products per page, 13 pages total.
ITEMS_PER_PAGE = 12


def test_scrapingcourse_pagination_advances_past_page_one(tmp_path: Path) -> None:
    output_path = tmp_path / "scrapingcourse_pagination_live.jsonl"

    items = run_spider_live(
        "scrapingcourse_pagination.yaml",
        output_path,
        extra_settings={"CLOSESPIDER_PAGECOUNT": "2"},
    )

    assert len(items) > ITEMS_PER_PAGE, (
        f"expected more than {ITEMS_PER_PAGE} products (one page) after following the "
        f"'Next page' link; got {len(items)}"
    )
    first = items[0]
    assert first["name"], "product name should not be empty"
    assert first["price"], "product price should not be empty"
