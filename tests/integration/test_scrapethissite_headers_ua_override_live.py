"""Integration test: `user_agent_override` (docs/REQUIREMENTS.md section 9
entry 24/27) against the exact real target that first
surfaced the gap -- scrapethissite.com/pages/advanced/?gotcha=headers,
now driven through the 3 AntibotProvider implementations directly
(``antibot_needed: true``), not the original round's ``render_js: true``
workaround.

Camoufox/Patchright: each config's own `user_agent_override` is a real,
browser-shaped User-Agent that is neither provider's own default -- a
pass here means the site accepted a request whose User-Agent this
provider does not send by default, real end-to-end evidence the override
reached the live browser context. What this alone cannot prove (the site
has no way to echo the exact string back) is closed by
test_camoufox_provider.py's/test_patchright_provider.py's own unit tests,
which assert the exact override string reaches the injected solve_fn
verbatim -- together, the two levels are the real proof.

Byparr: a real, confirmed structural gap (Byparr's own request payload
model has no userAgent field at all) -- this test instead proves the
documented graceful-degradation path for real: the override is silently
unsupported, but the crawl still succeeds via Byparr's own real,
unmodified default User-Agent. Skips (not fails) without a real Byparr
instance (TITAN_BYPARR_URL), the same as every other Byparr-dependent
live test in this suite.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from tests.integration._live_helpers import run_spider_live

BYPARR_URL = os.environ.get("TITAN_BYPARR_URL")


def _assert_headers_properly_spoofed(items: list[dict[str, object]], provider_name: str) -> None:
    assert len(items) == 1, (
        f"[{provider_name}] expected exactly 1 status-message item, got {len(items)}"
    )
    message = str(items[0]["message"])
    assert "properly spoofed" in message, (
        f"[{provider_name}] expected the real 'Headers properly spoofed' success "
        f"message -- got: {message!r}"
    )


def test_user_agent_override_via_camoufox_passes_the_real_site_check(tmp_path: Path) -> None:
    output_path = tmp_path / "scrapethissite_headers_ua_override_camoufox_live.jsonl"

    items = run_spider_live(
        "scrapethissite_advanced_headers_ua_override_camoufox.yaml", output_path
    )

    _assert_headers_properly_spoofed(items, "camoufox")


def test_user_agent_override_via_patchright_passes_the_real_site_check(tmp_path: Path) -> None:
    output_path = tmp_path / "scrapethissite_headers_ua_override_patchright_live.jsonl"

    items = run_spider_live(
        "scrapethissite_advanced_headers_ua_override_patchright.yaml", output_path
    )

    _assert_headers_properly_spoofed(items, "patchright")


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no Byparr instance running)"
)
def test_user_agent_override_via_byparr_is_ignored_gracefully(tmp_path: Path) -> None:
    """Byparr can't honor the override at all (real, source-confirmed gap)
    -- the crawl must still succeed via Byparr's own real default
    User-Agent, not crash or silently return nothing."""
    output_path = tmp_path / "scrapethissite_headers_ua_override_byparr_live.jsonl"

    items = run_spider_live("scrapethissite_advanced_headers_ua_override_byparr.yaml", output_path)

    _assert_headers_properly_spoofed(items, "byparr")
