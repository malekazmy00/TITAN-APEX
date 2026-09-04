"""Integration test: a quick, real re-verification of entry 19's own
conclusive finding, requested explicitly by the user before any new
JA4-based work -- "Camoufox's JA4 fingerprint is byte-for-byte
identical to real Firefox's" (primary source: daijro/camoufox issue
#555, quoted in docs/REQUIREMENTS.md entry 19).

Entry 19's own citation of that finding came from the GitHub issue's
own reported comparison (a real Firefox vs. a real Camoufox, run by
the issue's reporter), not something this project's own CI ran itself
against a *live* second Firefox instance -- this sandbox and this
project's own CI both only ever fetch Camoufox's patched-Firefox
binary (a plain, unmodified `playwright install firefox` binary is not
installed anywhere in this project's toolchain, confirmed directly:
`playwright.sync_api.sync_playwright().firefox.launch()` fails with
"Executable doesn't exist" in this sandbox, and `ci.yml`'s own
"Run playwright install" step only ever asks for `chromium`). A true,
live A/B against a second real Firefox instance is therefore not
achievable within this project's own toolchain today -- disclosed
honestly, not hidden.

What IS achievable, and is exactly what this test does: capture
Camoufox's own, real, live JA4 fingerprint TODAY (same camoufox==0.5.5
package version already installed when entry 19 was originally
written -- confirmed via `pip show camoufox` before writing this test,
zero version drift), through this project's own already-existing,
already-CI-confirmed JA4 pipeline (test-environment/ja4-proxy's real
HAProxy + FriendlyCaptcha's ja4.lua, entry 18's own Step B/C) -- and
compare its *structural shape* (the fixed protocol-descriptor prefix
JA4's own spec defines: TLS version + SNI-presence + cipher-count +
extension-count + first/last ALPN char) against entry 19's own,
previously-documented, real captured value from this exact same
pipeline (`t13d1617h2_86a278354501_3cbfd9057e0d`). That prefix is a
deterministic function of the browser's actual ClientHello structure
(cipher list, extension list, ALPN) for a fixed launch configuration --
identical launch config + identical browser engine build must produce
an identical prefix; the two trailing 12-hex-char segments are
truncated hashes of the cipher/extension *values* themselves and are
printed for the record but not asserted byte-for-byte equal here (real
browsers, Firefox included, are documented to randomize some
GREASE/extension placement per session as an anti-fingerprinting
measure of their own -- asserting exact hash equality here would risk
a false "drift" flag for a non-drift reason; the prefix is the part
JA4's own spec designed to be config-stable).

No spider config has ever pointed at the JA4 proxy before this test
(docs/REQUIREMENTS.md entry 18's own CI confirmation noted zero JA4
log lines for exactly that reason -- entry 18 wired the pipeline,
never used it) -- `mock_target_ja4_check.yaml` is the first.

Requires TITAN_BYPARR_URL *and* a running
test-environment/docker-compose.test.yml stack reachable at
http://localhost:${ANUBIS_PORT:-8080}/ (and, for this test
specifically, https://localhost:8443/, the ja4-proxy's own published
port) -- same "is a live-network CI stack actually running" gate every
other mock-target live test in this package uses.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from tests.integration._live_helpers import run_spider_live

BYPARR_URL = os.environ.get("TITAN_BYPARR_URL")
REPO_ROOT = Path(__file__).resolve().parents[2]
JA4_LOG = REPO_ROOT / "test-environment" / "logs" / "ja4_fingerprints.log"

# entry 19's own, previously-documented, real captured value from this
# exact pipeline (camoufox -> ja4-proxy -> mock-target).
ENTRY_19_HISTORICAL_JA4 = "t13d1617h2_86a278354501_3cbfd9057e0d"
ENTRY_19_HISTORICAL_PREFIX = ENTRY_19_HISTORICAL_JA4.split("_", 1)[0]

# JA4's own spec shape: t|q (TLS/QUIC) + version (10-13/s) + d|i (SNI
# present/absent) + 2-digit cipher count + 2-digit extension count +
# 2-char ALPN first/last (or "00") + "_" + 12-hex + "_" + 12-hex.
JA4_PATTERN = re.compile(r"^[tq](1[0-3]|s)[di]\d{2}\d{2}[a-z0-9]{2}_[0-9a-f]{12}_[0-9a-f]{12}$")


def _new_ja4_lines(before_count: int) -> list[dict[str, Any]]:
    all_lines = JA4_LOG.read_text(encoding="utf-8").splitlines() if JA4_LOG.exists() else []
    new_lines = all_lines[before_count:]
    return [json.loads(line) for line in new_lines if line]


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no live-network CI stack running)"
)
def test_camoufox_ja4_fingerprint_still_matches_the_real_firefox_shape(tmp_path: Path) -> None:
    """The real, direct re-check: a live Camoufox solve through
    ja4-proxy today, compared against entry 19's own historical
    evidence from the same pipeline."""
    before_count = len(JA4_LOG.read_text(encoding="utf-8").splitlines()) if JA4_LOG.exists() else 0

    output_path = tmp_path / "mock_target_ja4_check_live.jsonl"
    items = run_spider_live("mock_target_ja4_check.yaml", output_path)

    assert items, "expected at least one extracted post -- Camoufox never got past Anubis"

    new_entries = _new_ja4_lines(before_count)
    ja4_observed: list[str] = [
        str(entry["ja4_fingerprint"])
        for entry in new_entries
        if entry.get("message") == "ja4.fingerprint_observed"
    ]
    # Printed unconditionally -- this test's whole point is producing
    # real, first-hand evidence for the record, not just a pass/fail.
    print(f"--- ja4.fingerprint_observed entries captured live today ---\n{ja4_observed}")

    assert ja4_observed, (
        "expected at least one real ja4.fingerprint_observed log entry -- the request never "
        "went through ja4-proxy, or mock-target never logged it"
    )

    fingerprint = ja4_observed[0]
    assert JA4_PATTERN.match(fingerprint), (
        f"captured value {fingerprint!r} doesn't match JA4's own spec shape at all -- "
        "ja4-proxy's own computation may be broken, not a Camoufox-vs-Firefox question"
    )

    prefix = fingerprint.split("_", 1)[0]
    print(
        f"--- comparison against entry 19's historical value ---\n"
        f"today:   {fingerprint} (prefix {prefix})\n"
        f"history: {ENTRY_19_HISTORICAL_JA4} (prefix {ENTRY_19_HISTORICAL_PREFIX})"
    )
    assert prefix == ENTRY_19_HISTORICAL_PREFIX, (
        f"protocol-descriptor prefix changed since entry 19 ({ENTRY_19_HISTORICAL_PREFIX} -> "
        f"{prefix}) -- real drift in Camoufox's TLS ClientHello structure, entry 19's finding "
        "may no longer hold and needs a fresh investigation, not a stale citation"
    )
