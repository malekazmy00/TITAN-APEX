"""Integration test: src/spiders/configs/mock_target_camoufox.yaml against
the real, live test-environment/ stack -- the same target and challenge as
test_mock_target_live.py, this time via `antibot_provider: camoufox`
(docs/REQUIREMENTS.md section 9 entry 4 / round 3's first real test of
SpiderConfig's provider-selection field).

Requires the same TITAN_BYPARR_URL-gated live-CI signal every other test
in this package uses (Camoufox itself needs no external service -- it
drives its own browser in-process -- but the test-environment/ stack
still needs to be up, which the CI workflow brings up alongside Byparr;
skips cleanly, same as every other live test, in a plain local
`pytest tests/unit` run).

Real, expected result, based on CamoufoxProvider's actual design (not
assumed -- see docs/REQUIREMENTS.md section 9 entry 4 and
src/providers/antibot/camoufox_provider.py's own docstring for the full
reasoning): unlike Byparr, Camoufox drives its browser in-process, in the
same network namespace as Scrapy itself, so the network-namespace
mismatch that blocked Byparr from ever reaching Anubis in CI
(docs/REQUIREMENTS.md section 9 entry 1) structurally cannot happen here.
And unlike Byparr's `/v1` API, CamoufoxProvider holds the browser open a
configurable extra `post_load_wait_ms` (default 5s) after the page's
`load` event before reading content -- giving Anubis's real, asynchronous
post-load proof-of-work flow (docs/REQUIREMENTS.md section 9 entry 4) an
actual chance to finish. If both of those hold up for real, the crawl
should find real posts this time, not the challenge page.

This assertion is not an aspiration written before the fact and left
unchecked -- see the module's own git history/PR for the real CI run this
was confirmed (or corrected) against, the same discipline already applied
to test_mock_target_live.py's own docstring once round 1's CI run showed
a different real mechanism than local testing had found.
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
def test_mock_target_camoufox_gets_past_anubis_and_yields_real_posts(tmp_path: Path) -> None:
    output_path = tmp_path / "mock_target_camoufox_live.jsonl"

    items = run_spider_live("mock_target_camoufox.yaml", output_path, timeout=180)

    assert len(items) > 0, (
        "expected real posts -- Camoufox should get past Anubis's challenge "
        "this time (see this test's module docstring for why); got 0 items"
    )
    first = items[0]
    assert first["post_id"], "post_id should not be empty"
    assert first["author"], "author should not be empty"
