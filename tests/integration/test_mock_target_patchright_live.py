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

**Confirmed for real (CI run
[32524934383](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32524934383)):
Patchright does NOT get past Anubis's real challenge here -- and it fails
for a materially different, more fundamental reason than Byparr's gap
(docs/REQUIREMENTS.md section 9 entry 4) or anything `post_load_wait_ms`
could ever fix.** `patchright_provider.solved`'s own diagnostic log showed
`"title": "Oh noes!"` (Anubis's deny/error page) with `"cookie_names": []`
-- and Anubis's own container log (added to the CI workflow specifically
for this kind of root-causing) shows exactly why:

```
"msg":"explicit deny", ... "check_result":{"name":"bot/headless-chrome","rule":"DENY","weight":0}
```

Anubis has a built-in fingerprint rule that recognizes headless Chromium
by its automation signature and denies it outright -- *before* even
issuing a proof-of-work challenge. Patchright's stealth layer patches
Chromium's automation fingerprints, but not enough to evade this specific
rule in this real deployment. Unlike Byparr (which genuinely reaches
Anubis and receives a real challenge every time, but tears its browser
down before finishing it, entry 4) or Camoufox (which gets past this
exact challenge because it drives a *Firefox*-based browser Anubis's
`bot/headless-chrome` rule was never written to match, entry 5), Patchright
never even reaches the challenge stage at all: this is a browser-engine
fingerprint match, not a timing gap -- `post_load_wait_ms` (Patchright's
whole reason for existing, mirroring Camoufox's) never gets a chance to
matter here, because the request is already denied by the time `load`
fires.

This assertion is a regression sentinel, not an aspiration -- the same
convention `test_mock_target_live.py` already established for Byparr's own
confirmed gap: if this ever changes (Anubis's rule changes, Patchright's
stealth layer improves, or a different provider is selected) and the crawl
starts finding real posts, this test should be updated to match.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from tests.integration._live_helpers import run_spider_live

BYPARR_URL = os.environ.get("TITAN_BYPARR_URL")
REPO_ROOT = Path(__file__).resolve().parents[2]
HONEYPOT_LOG = REPO_ROOT / "test-environment" / "logs" / "honeypot_triggers.log"


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no live-network CI stack running)"
)
def test_mock_target_patchright_yields_zero_items_denied_by_anubis(tmp_path: Path) -> None:
    """Documents the real, confirmed outcome (see module docstring for the
    full, evidenced root-cause: Anubis's own "bot/headless-chrome" explicit
    DENY rule, not a load-timing gap)."""
    output_path = tmp_path / "mock_target_patchright_live.jsonl"

    items = run_spider_live("mock_target_patchright.yaml", output_path, timeout=180)

    assert items == [], (
        f"expected zero items (see this test's module docstring for the "
        f"real, evidenced reason -- Anubis's explicit bot/headless-chrome "
        f"deny rule); got {len(items)}: {items[:1]}"
    )


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no live-network CI stack running)"
)
def test_mock_target_patchright_crawl_never_reaches_a_real_honeypot(tmp_path: Path) -> None:
    """Same reasoning as test_mock_target_live.py's identical check: since
    the crawl above never gets past Anubis's explicit deny at all, nothing
    here should be logged from this run."""
    before_size = HONEYPOT_LOG.stat().st_size if HONEYPOT_LOG.exists() else None

    output_path = tmp_path / "mock_target_patchright_live_honeypot_check.jsonl"
    run_spider_live("mock_target_patchright.yaml", output_path, timeout=180)

    after_size = HONEYPOT_LOG.stat().st_size if HONEYPOT_LOG.exists() else None
    assert after_size == before_size, (
        "honeypot_triggers.log grew during a crawl that should never have "
        "reached real mock-target content -- see this module's docstring"
    )
