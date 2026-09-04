"""Integration test: docs/PHASE_2_BACKLOG.md item 5 (WebRTC Leak
Prevention) -- the real, live confirmation that
CamoufoxProvider's new ``block_webrtc`` parameter
(``src/core/interfaces/antibot_provider.py``) actually prevents a real
local/network IP from leaking through WebRTC ICE candidates.

Reads test-environment/logs/webrtc_leak_reports.log -- written
server-side by ``/webrtc-leak-report`` (test-environment/mock-target/
app.py), itself populated by the real client-side JS
``/webrtc-leak-check`` serves, which creates a real
``RTCPeerConnection`` and reports back every ICE candidate it actually
gathered (or that the API was unavailable at all -- Camoufox's own
``block_webrtc`` removes ``RTCPeerConnection`` from JS entirely by
setting Firefox's ``media.peerconnection.enabled`` pref to ``False``).

Requires TITAN_BYPARR_URL *and* a running
test-environment/docker-compose.test.yml stack reachable at
http://localhost:${ANUBIS_PORT:-8080}/ -- same "is a live-network CI
stack actually running" gate every other mock-target live test in this
package uses.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from tests.integration._live_helpers import run_spider_live

BYPARR_URL = os.environ.get("TITAN_BYPARR_URL")
REPO_ROOT = Path(__file__).resolve().parents[2]
WEBRTC_LEAK_LOG = REPO_ROOT / "test-environment" / "logs" / "webrtc_leak_reports.log"


def _last_report() -> dict[str, object]:
    lines = (
        WEBRTC_LEAK_LOG.read_text(encoding="utf-8").splitlines()
        if WEBRTC_LEAK_LOG.exists()
        else []
    )
    assert lines, "expected at least one webrtc_leak.checked log entry -- none were ever written"
    return dict(json.loads(lines[-1]))


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no live-network CI stack running)"
)
def test_block_webrtc_prevents_a_real_ip_leak(tmp_path: Path) -> None:
    """The real, direct evidence this whole entry is about: with
    block_webrtc: true, RTCPeerConnection is unavailable to the page's
    own JS at all -- confirmed from the real log the live page itself
    wrote, not assumed from the config alone."""
    output_path = tmp_path / "mock_target_webrtc_leak_check_live.jsonl"

    run_spider_live("mock_target_webrtc_leak_check.yaml", output_path)

    report = _last_report()
    # Printed unconditionally -- same discipline entry 30.2 established:
    # real, first-hand evidence in the CI log, not just a pass/fail.
    print(f"--- webrtc leak report (block_webrtc=true) ---\n{report}")

    assert report["webrtc_available"] is False, (
        "expected block_webrtc=True to remove RTCPeerConnection from JS entirely -- got "
        f"webrtc_available={report['webrtc_available']!r}, meaning the API was still there"
    )
    assert report["leak_detected"] is False
    assert report["leaked_addresses"] == []


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no live-network CI stack running)"
)
def test_baseline_without_block_webrtc_documents_the_real_current_behavior(
    tmp_path: Path,
) -> None:
    """The comparative baseline: same page, same browser, block_webrtc
    simply never set (every existing target's real, current behavior
    today). Documents whatever actually happens with real evidence --
    does not assume the backlog's own "real gap" framing still holds
    unchanged (modern Firefox's own mDNS ICE-candidate obfuscation,
    active by default for years, may already mask the real local IP
    even without this project's own fix)."""
    output_path = tmp_path / "mock_target_webrtc_leak_check_baseline_live.jsonl"

    run_spider_live("mock_target_webrtc_leak_check_baseline.yaml", output_path)

    report = _last_report()
    print(f"--- webrtc leak report (block_webrtc unset, baseline) ---\n{report}")

    # webrtc_available is expected True here (nothing disabled the API) --
    # this is the one part of the baseline that is *not* in question.
    assert report["webrtc_available"] is True
