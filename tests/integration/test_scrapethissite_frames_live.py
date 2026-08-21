"""Integration test: scrapethissite.com/pages/frames (Level 2,
docs/TEST_TARGETS.md).

The wrapper page is just an empty <iframe> shell -- GenericSpider does
not execute JS or follow iframe sources -- so scrapethissite_frames.yaml
points start_urls directly at the iframe's own document URL
(?frame=i). This test proves that config-only fix actually yields the
turtle-family data against the real, live site.
"""

from __future__ import annotations

from pathlib import Path

from tests.integration._live_helpers import run_spider_live

# Confirmed by investigation: 14 turtle-family cards on the iframe page.
EXPECTED_FAMILY_COUNT = 14


def test_scrapethissite_frames_iframe_url_yields_turtle_families(tmp_path: Path) -> None:
    output_path = tmp_path / "scrapethissite_frames_live.jsonl"

    items = run_spider_live("scrapethissite_frames.yaml", output_path)

    assert len(items) == EXPECTED_FAMILY_COUNT, (
        f"expected {EXPECTED_FAMILY_COUNT} turtle families, got {len(items)}"
    )
    first = items[0]
    assert first["family_name"], "family name should not be empty"
    assert first["image_url"], "image url should not be empty"
