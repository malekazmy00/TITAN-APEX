"""Integration test: scrapethissite.com/pages/simple (Level 1,
docs/TEST_TARGETS.md).

Static HTML, all 250 countries on one page -- no pagination, no JS. Runs
for real in CI against the live site.
"""

from __future__ import annotations

from pathlib import Path

from tests.integration._live_helpers import run_spider_live

# Confirmed by investigation: exactly 250 "div.country" rows on the page.
EXPECTED_COUNTRY_COUNT = 250


def test_scrapethissite_simple_yields_all_countries(tmp_path: Path) -> None:
    output_path = tmp_path / "scrapethissite_simple_live.jsonl"

    items = run_spider_live("scrapethissite_simple.yaml", output_path)

    assert len(items) == EXPECTED_COUNTRY_COUNT, (
        f"expected {EXPECTED_COUNTRY_COUNT} countries, got {len(items)}"
    )
    first = items[0]
    assert first["name"], "country name should not be empty"
    assert first["capital"], "capital should not be empty"
