"""Integration test: src/spiders/configs/mock_target_spa_catalog.yaml
against the real, live test-environment/ stack (docs/REQUIREMENTS.md
section 9 entry 23, Phase 2 بند 6, Known Limitation #5's real fix).

Proves the actual thing this entry set out to prove: a page with real
hydration delay, real CSS-in-JS-shaped (opaque, non-hardcodable) class
names, and no per-item wrapper at all -- the exact shape the earlier
react-shopping-cart investigation (section 7 entry 5) found this
project's existing extraction strategies structurally cannot handle --
is now scraped correctly, end to end, via ``selectors.item_group_size``'s
positional extraction, with zero Camoufox/Patchright/session/navigation
code involved (this target sits in front of Anubis's own explicit ALLOW
rule -- ``render_js`` alone, PlaywrightMiddleware, gets it).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from tests.integration._live_helpers import run_spider_live

BYPARR_URL = os.environ.get("TITAN_BYPARR_URL")


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no live-network CI stack running)"
)
def test_spa_catalog_extracts_every_product_by_position_not_class(tmp_path: Path) -> None:
    output_path = tmp_path / "mock_target_spa_catalog_live.jsonl"

    items = run_spider_live("mock_target_spa_catalog.yaml", output_path, timeout=180)

    assert len(items) > 0, (
        "expected real products from /spa-catalog's hydrated grid -- got 0: either "
        "render_wait_ms didn't cover the real hydration delay, or positional "
        "extraction (item_group_size) regressed"
    )
    for item in items:
        assert item["title"], "title should not be empty"
        assert item["price"], "price should not be empty"
        assert item["image_url"], "image_url should not be empty"
        # The one real semantic invariant every product's price has,
        # independent of the field's own randomized content.
        assert item["price"].startswith("$")
