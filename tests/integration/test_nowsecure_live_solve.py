"""Integration test: Byparr against nowsecure.nl's Cloudflare Turnstile
demo (Tier 2 List A, docs/TEST_TARGETS.md).

This is a second, independent Cloudflare Turnstile target beyond
scrapingcourse.com/cloudflare-challenge, to confirm the earlier pass
wasn't luck specific to one site. Note a real difference found during
investigation: unlike cloudflare-challenge (a network/WAF-level block --
a plain, un-rendered fetch never gets real content), nowsecure.nl's
Turnstile widget is purely client-side and cosmetic -- a plain
unauthenticated fetch already returns 200 with the full page (confirmed
by a direct request), and the widget uses Cloudflare's own published
"always passes" test sitekey (3x00000000000000000000FF). So a pass here
mainly proves Byparr's browser executes the page and the Turnstile
widget's JS without erroring, not that it defeats an access restriction
the way the WAF-level target does -- see the Test Targets report for
this distinction spelled out.

Requires a running Byparr instance (TITAN_BYPARR_URL); skips cleanly
(not a failure) if it isn't set, same as test_byparr_live_solve.py.
"""

from __future__ import annotations

import os

import pytest

from src.providers.antibot.byparr_provider import ByparrProvider

BYPARR_URL = os.environ.get("TITAN_BYPARR_URL")


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no Byparr instance running)"
)
def test_byparr_renders_the_nowsecure_turnstile_demo() -> None:
    assert BYPARR_URL  # guarded by skipif above; narrows type for mypy too
    provider = ByparrProvider(base_url=BYPARR_URL, timeout_ms=90_000)

    solution = provider.solve("https://nowsecure.nl/")

    assert solution.status_code == 200
    assert len(solution.html) > 500
