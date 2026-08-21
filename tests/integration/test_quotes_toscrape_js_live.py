"""Integration test: quotes.toscrape.com/js (Level 2, docs/TEST_TARGETS.md).

Same quotes as quotes_toscrape.yaml, but rendered client-side from an
embedded JS array with the same "li.next a" pagination pattern. Capped to
2 pages via CLOSESPIDER_PAGECOUNT -- each page is a separate Playwright
browser launch, so this keeps the shared "Integration tests" step fast.
"""

from __future__ import annotations

from pathlib import Path

from tests.integration._live_helpers import run_spider_live

# Confirmed by investigation (same shape as quotes_toscrape.yaml): 10 per page.
QUOTES_PER_PAGE = 10


def test_quotes_toscrape_js_pagination_advances_past_page_one(tmp_path: Path) -> None:
    output_path = tmp_path / "quotes_toscrape_js_live.jsonl"

    items = run_spider_live(
        "quotes_toscrape_js.yaml",
        output_path,
        extra_settings={"CLOSESPIDER_PAGECOUNT": "2"},
    )

    assert len(items) > QUOTES_PER_PAGE, (
        f"expected more than {QUOTES_PER_PAGE} JS-rendered quotes (one page); got {len(items)}"
    )
    first = items[0]
    assert first["text"], "quote text should not be empty"
    assert first["author"], "author should not be empty"
