"""Integration test: scrapethissite.com/pages/ajax-javascript (Level 2,
docs/TEST_TARGETS.md).

Second attempt at this target: the first (start URL "#2015" hash trick
alone, no render_wait_ms) passed once in CI (run 32443029436) then
failed once (run 32471707326, assert 0 > 0) with the exact same config
-- a genuine, evidenced timing race with the site's own ~1.5s
post-AJAX-response render delay, not a fluke worth re-running past.

scrapethissite_ajax_javascript.yaml now also sets render_wait_ms: 2500,
a config-only fixed extra wait PlaywrightMiddleware applies after its
scroll loop and before reading page.content() -- see
docs/REQUIREMENTS.md section 7 entry 3 for the full history. This test
is what proves whether that actually closes the gap for real.
"""

from __future__ import annotations

from pathlib import Path

from tests.integration._live_helpers import run_spider_live


def test_scrapethissite_ajax_javascript_yields_rows_after_hash_triggered_fetch(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "scrapethissite_ajax_javascript_live.jsonl"

    items = run_spider_live("scrapethissite_ajax_javascript.yaml", output_path)

    assert len(items) > 0, (
        "expected at least one film row after the '#2015' hash triggered the AJAX fetch "
        "and render_wait_ms gave the site's own ~1.5s render delay time to finish; got none"
    )
    first = items[0]
    assert first["title"], "film title should not be empty"
