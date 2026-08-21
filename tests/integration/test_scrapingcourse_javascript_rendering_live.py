"""Integration test: scrapingcourse.com/javascript-rendering (Level 3,
docs/TEST_TARGETS.md).

The static HTML ships 13 empty product placeholders (name/price spans
present but with no text) -- content is injected entirely by JS. Proves
render_js actually fills them in against the real, live site.
"""

from __future__ import annotations

from pathlib import Path

from tests.integration._live_helpers import run_spider_live


def test_scrapingcourse_javascript_rendering_fills_in_product_names(tmp_path: Path) -> None:
    output_path = tmp_path / "scrapingcourse_javascript_rendering_live.jsonl"

    items = run_spider_live("scrapingcourse_javascript_rendering.yaml", output_path)

    assert len(items) > 0, "expected at least one JS-rendered product item"
    first = items[0]
    assert first["name"], "product name should not be empty after JS rendering"
    assert first["price"], "product price should not be empty after JS rendering"
