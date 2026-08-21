"""Diagnostic integration test: bot.sannysoft.com (Tier 2 List A,
docs/TEST_TARGETS.md).

Not a scraping target -- bot.sannysoft.com is a browser-fingerprint
*diagnostic* page: its own JS flags each automation-detection check as
"passed"/"failed" (a CSS class on the result cell) once it runs in a
real browser. The project's stated use for it is a verification step
after any change to ByparrProvider/stealth args, not a data source, so
this test does not gate CI on individual stealth checks passing or
failing -- Byparr's job is anti-bot *solving*, not full undetectability,
and a "failed" row here is useful information, not necessarily a bug.

It asserts only that the page was fetched successfully through Byparr,
and prints a parsed summary of every check's pass/fail state so it's
visible as real evidence in the CI log -- exactly the "screenshot/result
of the check" record docs/TEST_TARGETS.md asks for after a bypass
attempt.

Requires a running Byparr instance (TITAN_BYPARR_URL); skips cleanly
(not a failure) if it isn't set, same as test_byparr_live_solve.py.
"""

from __future__ import annotations

import os

import pytest
from parsel import Selector

from src.providers.antibot.byparr_provider import ByparrProvider

BYPARR_URL = os.environ.get("TITAN_BYPARR_URL")


def _summarize_checks(html: str) -> dict[str, str]:
    """Map each "<td id='...-result'>" check to its passed/failed state.

    Falls back to "unknown" for a cell whose class carries neither
    marker (e.g. the page's JS didn't run at all) rather than guessing.
    """
    selector = Selector(text=html)
    summary: dict[str, str] = {}
    for cell in selector.css('td[id$="-result"]'):
        check_id = cell.attrib.get("id", "")
        css_class = cell.attrib.get("class", "")
        if "passed" in css_class:
            summary[check_id] = "passed"
        elif "failed" in css_class:
            summary[check_id] = "failed"
        else:
            summary[check_id] = "unknown"
    return summary


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no Byparr instance running)"
)
def test_sannysoft_diagnostic_via_byparr() -> None:
    assert BYPARR_URL  # guarded by skipif above; narrows type for mypy too
    provider = ByparrProvider(base_url=BYPARR_URL, timeout_ms=90_000)

    solution = provider.solve("https://bot.sannysoft.com/")

    assert solution.status_code == 200
    assert len(solution.html) > 500

    checks = _summarize_checks(solution.html)
    assert checks, "expected at least one '*-result' diagnostic cell in the rendered page"
    print(  # noqa: T201 -- deliberate CI-log evidence, the whole point of this test
        f"sannysoft stealth diagnostic via Byparr: {checks}"
    )
