"""Integration test: books.toscrape.com (Level 1, docs/TEST_TARGETS.md).

Static HTML, no anti-bot, no JS -- pagination + text extraction against a
real live site. Runs for real in CI (unrestricted internet); capped to 2
pages via CLOSESPIDER_PAGECOUNT to keep the shared "Integration tests"
step fast (proving pagination works doesn't require crawling all 50
pages).
"""

from __future__ import annotations

from pathlib import Path

from tests.integration._live_helpers import run_spider_live

# Confirmed by investigation: 20 books per page.
ITEMS_PER_PAGE = 20


def test_books_toscrape_yields_more_than_one_page_of_books(tmp_path: Path) -> None:
    output_path = tmp_path / "books_toscrape_live.jsonl"

    items = run_spider_live(
        "books_toscrape.yaml",
        output_path,
        extra_settings={"CLOSESPIDER_PAGECOUNT": "2"},
    )

    assert len(items) > ITEMS_PER_PAGE, (
        f"expected more than {ITEMS_PER_PAGE} books (one page) after following pagination; "
        f"got {len(items)}"
    )
    first = items[0]
    assert first["title"], "title should not be empty"
    assert first["price"].startswith("£"), f"unexpected price format: {first['price']!r}"
