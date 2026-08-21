"""Integration test: src/spiders/configs/mock_target_patchright.yaml against
the real, live test-environment/ stack -- the same target and challenge as
test_mock_target_live.py / test_mock_target_camoufox_live.py, this time via
`antibot_provider: patchright` (this phase's revision: a third,
lighter-weight AntibotProvider -- Chromium + Patchright's stealth layer on
top of the Playwright this project already depends on, instead of a whole
separate Firefox-based stealth browser).

Requires the same TITAN_BYPARR_URL-gated live-CI signal every other test in
this package uses (PatchrightProvider itself needs no external service --
it drives its own browser in-process -- but the test-environment/ stack
still needs to be up, which the CI workflow brings up alongside Byparr;
skips cleanly, same as every other live test, in a plain local
`pytest tests/unit` run).

**Not yet confirmed either way** -- this is the first real run of this
provider against Anubis's actual proof-of-work challenge. Per this
project's own rule (docs/REQUIREMENTS.md, "verify, don't assume"), whether
Patchright actually gets past it is not assumed from Camoufox's own,
separately-confirmed success (CI run 32507637737): a different browser
engine (Chromium here, Firefox there) and a different stealth layer can
behave differently against the exact same challenge. Once this has run for
real in CI, this docstring (and docs/REQUIREMENTS.md's "Antibot Provider
Comparison" table) gets updated with the real, evidenced result -- success
or failure -- the same way test_mock_target_camoufox_live.py's own
docstring was rewritten only after its real CI evidence existed.
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
def test_mock_target_patchright_gets_past_anubis_and_yields_real_posts(tmp_path: Path) -> None:
    output_path = tmp_path / "mock_target_patchright_live.jsonl"

    items = run_spider_live("mock_target_patchright.yaml", output_path, timeout=180)

    assert len(items) > 0, (
        "expected real posts if Patchright gets past Anubis's challenge -- "
        "got 0 items; see this test's module docstring: this is the first "
        "real test of this provider against this stack, not yet confirmed "
        "either way"
    )
    first = items[0]
    assert first["post_id"], "post_id should not be empty"
    assert first["author"], "author should not be empty"
