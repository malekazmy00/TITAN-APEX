"""Integration test: scrapethissite.com/pages/forms (Level 1,
docs/TEST_TARGETS.md).

Static HTML, numbered pagination via a "Next" link. Investigation found a
real site quirk: the bare URL's "Next" link points back to page 1
(config works around it by starting at "?page_num=1" explicitly -- see
scrapethissite_forms.yaml). This test proves the workaround actually
lets pagination advance past page 1 against the real, live site.
"""

from __future__ import annotations

from pathlib import Path

from tests.integration._live_helpers import run_spider_live

# Confirmed by investigation: 25 team rows per page.
ROWS_PER_PAGE = 25


def test_scrapethissite_forms_pagination_advances_past_page_one(tmp_path: Path) -> None:
    output_path = tmp_path / "scrapethissite_forms_live.jsonl"

    items = run_spider_live(
        "scrapethissite_forms.yaml",
        output_path,
        extra_settings={"CLOSESPIDER_PAGECOUNT": "2"},
    )

    assert len(items) > ROWS_PER_PAGE, (
        f"expected more than {ROWS_PER_PAGE} rows (one page) after following pagination past "
        f"the buggy page-1 'Next' link; got {len(items)}"
    )
    first = items[0]
    assert first["name"], "team name should not be empty"
    assert first["year"], "year should not be empty"
