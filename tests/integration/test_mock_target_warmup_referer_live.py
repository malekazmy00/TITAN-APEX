"""Integration test: src/spiders/configs/mock_target_warmup_referer_check.yaml
against the real, live test-environment/ stack -- docs/REQUIREMENTS.md
section 9 entry 21, Step 1 (Referer path consistency + session
warm-up, Levels 1/2).

Deliberately a *plain* crawl -- no antibot solving at all
(``antibot_needed: false``, and ``test-environment/anubis/botPolicy.yaml``'s
own new ALLOW rule lets ``/warmup-home``/``/warmup-category``/
``/warmup-target`` straight through) -- so this test isolates the actual
question this step is about: does ``GenericSpider``'s own
``warm_session_urls`` chain (``_parse_warm_session_step``) produce a
real, connected Referer + session-cookie sequence via Scrapy's own
already-enabled ``RefererMiddleware``/``CookiesMiddleware``, not
something forged after the fact? The separate, much bigger question of
how a real, browser-driven antibot solve would carry a warm-up chain
through one continuous session is documented as a deferred Step 2, not
this test's job.

Requires TITAN_BYPARR_URL *and* a running
test-environment/docker-compose.test.yml stack reachable at
http://localhost:${ANUBIS_PORT:-8080}/ -- same "is a live-network CI
stack actually running" gate every other mock-target live test in this
package already uses (this test needs no Byparr instance itself, but
follows the same established convention rather than inventing a new
env var for the same underlying signal).
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


def _last_entry_for_path(log_lines: list[str], path: str) -> dict[str, object]:
    parsed = [json.loads(line) for line in log_lines if line]
    matches = [entry for entry in parsed if entry.get("path") == path]
    assert matches, f"no referer_session.checked log entry found for path {path!r}"
    return matches[-1]


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no live-network CI stack running)"
)
def test_warm_session_chain_produces_a_real_clean_referer_and_cookie_trail(
    tmp_path: Path,
) -> None:
    """The real, direct evidence this whole step is about: after a real
    warm_session_urls chain, mock-target's own server-side log shows
    both Level 1 and Level 2 scoring 0 for every hop that has a real
    predecessor -- not assumed, read back from the actual log file the
    live Flask app wrote during this exact run."""
    output_path = tmp_path / "mock_target_warmup_referer_check_live.jsonl"

    before_line_count = (
        len(REFERER_SESSION_LOG.read_text(encoding="utf-8").splitlines())
        if REFERER_SESSION_LOG.exists()
        else 0
    )

    items = run_spider_live("mock_target_warmup_referer_check.yaml", output_path)

    assert items == [
        {
            "source_url": "http://localhost:8080/warmup-target",
            "item_id": "1",
            "text": "Warm-up target reached",
        }
    ]

    all_lines = REFERER_SESSION_LOG.read_text(encoding="utf-8").splitlines()
    new_lines = all_lines[before_line_count:]
    assert new_lines, "expected new referer_session.checked log entries from this run"

    category_entry = _last_entry_for_path(new_lines, "/warmup-category")
    assert category_entry["level1_score"] == 0
    assert category_entry["level2_score"] == 0
    assert category_entry["has_warmup_session_cookie"] is True
    assert category_entry["referer"] == "http://localhost:8080/warmup-home"

    target_entry = _last_entry_for_path(new_lines, "/warmup-target")
    assert target_entry["level1_score"] == 0
    assert target_entry["level2_score"] == 0
    assert target_entry["has_warmup_session_cookie"] is True
    assert target_entry["referer"] == "http://localhost:8080/warmup-category"
