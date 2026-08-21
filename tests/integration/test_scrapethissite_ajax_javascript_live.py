"""Integration test: scrapethissite.com/pages/ajax-javascript (Level 2,
docs/TEST_TARGETS.md).

The static HTML ships an empty <tbody>; rows only appear once JS fires an
AJAX fetch, which only happens on a ".year-link" click or if
document.location.hash is already set on load -- scrapethissite_ajax_javascript.yaml
uses a "#2015" hash start URL to trigger it without any new spider code.

This is deliberately run for real against the live site rather than
assumed to work: the site also adds an ~1.5s client-side delay *after*
the AJAX response arrives before rendering rows, which may be longer than
PlaywrightMiddleware's post-navigation scroll dwell. If that's a real gap
it will show up here as a real, evidenced CI failure -- see the Test
Targets report for the actual result before this was assumed to be a
"pending" item (docs/REQUIREMENTS.md, section 3).
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
        "expected at least one film row after the '#2015' hash triggered the AJAX fetch; "
        "got none -- see the module docstring for the suspected timing race with the site's "
        "own ~1.5s post-response render delay"
    )
    first = items[0]
    assert first["title"], "film title should not be empty"
