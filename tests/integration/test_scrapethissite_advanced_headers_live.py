"""Integration test: scrapethissite.com/pages/advanced/?gotcha=headers
(Level 3, docs/ADVANCED_TEST_TARGETS_L3.md).

Investigated directly first (curl, not assumed): the page rejects a
request whose Accept header isn't text/html-first (Scrapy's own
default already satisfies this) *and* whose User-Agent doesn't look
like a real browser's (Scrapy's own default User-Agent fails this
outright, confirmed for real -- see
src/spiders/configs/scrapethissite_advanced_headers.yaml's own
comments). render_js: true is the only currently-possible way to pass
this specific check -- proves that end to end.
"""

from __future__ import annotations

from pathlib import Path

from tests.integration._live_helpers import run_spider_live


def test_scrapethissite_advanced_headers_reports_properly_spoofed(tmp_path: Path) -> None:
    output_path = tmp_path / "scrapethissite_advanced_headers_live.jsonl"

    items = run_spider_live("scrapethissite_advanced_headers.yaml", output_path)

    assert len(items) == 1, f"expected exactly 1 status-message item, got {len(items)}"
    assert "properly spoofed" in items[0]["message"], (
        f"expected the real 'Headers properly spoofed' success message -- got: "
        f"{items[0]['message']!r}"
    )
