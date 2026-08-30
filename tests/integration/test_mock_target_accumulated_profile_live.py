"""Integration test: src/spiders/configs/mock_target_accumulated_profile.yaml
against the real, live test-environment/ stack -- docs/REQUIREMENTS.md
section 9 entry 21, Step 2 (persistent browser context across warm-up +
a cross-call accumulated cookie jar).

Two real, separate claims this step made, both checked here with direct
evidence, not assumed:

1. **A continuous browser context across the warm-up chain and the real
   target, within one ``solve()`` call**: confirmed by hand already
   (docs/REQUIREMENTS.md entry 21's own writeup has the exact recorded
   cookie values) -- the real target's own returned cookies include the
   warm-up-only ``mocktarget_warmup_session`` cookie, something that
   could only be true if the warm-up navigation and the real target
   navigation shared one real browser cookie jar.
2. **A cross-*call* accumulated cookie jar**: this test's own real
   proof -- ``CamoufoxProvider``'s default ``cookie_jar_path``
   (``var/cookie_jar.json``, relative to the repo root, the same
   working directory every ``scrapy runspider`` subprocess this test
   package's own :func:`~tests.integration._live_helpers.run_spider_live`
   launches) is shared across *separate* real crawl runs on purpose.
   Running this exact config twice in a row, deleting the jar first,
   should show ``/warmup-home``'s own ``referer_session.checked`` log
   entry (this route logs whether an incoming request already carries
   the warm-up cookie, *before* deciding whether to issue a fresh one --
   see ``app.py``'s own ``warmup_home()``) go from
   ``has_warmup_session_cookie: false`` on the first, cold-jar run to
   ``true`` on the second -- direct, real evidence the accumulated
   profile actually carried into a *completely separate* browser
   process on the second run, not merely within the first one's own
   warm-up chain.

Requires TITAN_BYPARR_URL *and* a running
test-environment/docker-compose.test.yml stack (same "is a live-network
CI stack actually running" gate every other mock-target live test in
this package already uses).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from tests.integration._live_helpers import run_spider_live

BYPARR_URL = os.environ.get("TITAN_BYPARR_URL")
REPO_ROOT = Path(__file__).resolve().parents[2]
REFERER_SESSION_LOG = REPO_ROOT / "test-environment" / "logs" / "referer_session_reports.log"
# CamoufoxProvider's own real default -- see camoufox_provider.py's own
# DEFAULT_COOKIE_JAR_PATH.
COOKIE_JAR_PATH = REPO_ROOT / "var" / "cookie_jar.json"


def _warmup_home_entries(log_lines: list[str]) -> list[dict[str, object]]:
    parsed = [json.loads(line) for line in log_lines if line]
    return [entry for entry in parsed if entry.get("path") == "/warmup-home"]


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no live-network CI stack running)"
)
def test_accumulated_profile_carries_a_cookie_into_a_completely_separate_later_run(
    tmp_path: Path,
) -> None:
    """The real, decisive evidence for this whole step: a second,
    completely independent ``scrapy runspider`` subprocess (its own
    fresh Camoufox browser, no in-process state shared with the first
    run at all) already has the warm-up cookie by the time it reaches
    ``/warmup-home`` -- proof the accumulated jar genuinely persisted
    across real, separate solve() calls, not just within one."""
    COOKIE_JAR_PATH.unlink(missing_ok=True)
    assert not COOKIE_JAR_PATH.exists(), "expected a clean jar before this test's own first run"

    before_line_count = (
        len(REFERER_SESSION_LOG.read_text(encoding="utf-8").splitlines())
        if REFERER_SESSION_LOG.exists()
        else 0
    )

    # --- Run 1: cold jar -----------------------------------------------
    items_run1 = run_spider_live(
        "mock_target_accumulated_profile.yaml", tmp_path / "run1.jsonl", timeout=180
    )
    assert items_run1, "expected the first run to extract real /feed items"
    assert COOKIE_JAR_PATH.is_file(), "expected a successful solve to have written the jar"

    all_lines = REFERER_SESSION_LOG.read_text(encoding="utf-8").splitlines()
    run1_new_lines = all_lines[before_line_count:]
    run1_warmup_home_entries = _warmup_home_entries(run1_new_lines)
    assert run1_warmup_home_entries, "expected a /warmup-home log entry from run 1"
    assert run1_warmup_home_entries[-1]["has_warmup_session_cookie"] is False, (
        "run 1 started from a genuinely cold, empty jar -- it must not already "
        "carry the warm-up cookie"
    )

    before_line_count_run2 = len(all_lines)

    # --- Run 2: same jar, a completely separate subprocess/browser -----
    items_run2 = run_spider_live(
        "mock_target_accumulated_profile.yaml", tmp_path / "run2.jsonl", timeout=180
    )
    assert items_run2, "expected the second run to also extract real /feed items"

    all_lines_after_run2 = REFERER_SESSION_LOG.read_text(encoding="utf-8").splitlines()
    run2_new_lines = all_lines_after_run2[before_line_count_run2:]
    run2_warmup_home_entries = _warmup_home_entries(run2_new_lines)
    assert run2_warmup_home_entries, "expected a /warmup-home log entry from run 2"
    assert run2_warmup_home_entries[-1]["has_warmup_session_cookie"] is True, (
        "run 2 should have started from run 1's own accumulated jar -- the "
        "warm-up cookie should already be present before /warmup-home even runs"
    )

    COOKIE_JAR_PATH.unlink(missing_ok=True)
