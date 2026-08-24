"""Integration test:
src/spiders/configs/mock_target_login_protected.yaml,
mock_target_login_protected_session_expiry.yaml, and
mock_target_feed_protected_no_login.yaml against the real, live
test-environment/ stack -- docs/REQUIREMENTS.md section 9 entry 15
(Known Limitation #1: login/session, activated ahead of Interstitials
per explicit user request).

**The real, open question these tests answer:** can ``GenericSpider``,
via a real, live-browser-driving provider, genuinely perform
``GET /login`` -> parse a fresh, per-load CSRF token -> ``POST``
credentials+token -> hold the resulting session cookie -> reach
``/feed-protected``'s real, otherwise-401-gated data, all through
Anubis's own real proof-of-work challenge (every route on this stack
sits behind it, including ``/login`` and ``/feed-protected`` -- there is
no path-based exemption in this stack's own ``botPolicy.yaml``, checked
directly)? And does an attempt with no valid session (no login at all,
or one that was deliberately, deterministically expired mid-crawl) come
back a real, explicit 401 -- logged clearly, not a crash and not a
silent empty result?

**A real, discovered architectural constraint this whole design works
around, not assumes away** (see ``src.core.interfaces.antibot_provider.LoginFlow``'s
own docstring for the full explanation): each ``AntibotProvider.solve()``
call launches its own fresh browser -- cookies never persist *across*
separate ``solve()`` calls. So the entire login -> (optional test-only
expiry probe) -> target-fetch sequence happens *within one* ``solve()``
call, in one continuous browser session, not across separate
Scrapy-level requests sharing a cookie jar.

Requires the same TITAN_BYPARR_URL-gated live-CI signal every other test
in this package uses.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from tests.integration._live_helpers import run_spider_live

BYPARR_URL = os.environ.get("TITAN_BYPARR_URL")

# mock-target/config.py's own default (PROTECTED_FEED_PAGE_SIZE) -- a
# single, un-paginated /feed-protected fetch (no next_page following in
# these configs, deliberately: this round is about login/session
# mechanics, not re-testing pagination) returns exactly this many posts
# once past a valid session.
EXPECTED_PROTECTED_PAGE_SIZE = 5


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no live-network CI stack running)"
)
def test_login_flow_reaches_protected_data_after_a_real_post_and_csrf_token(
    tmp_path: Path,
) -> None:
    """Happy path: a real GET /login -> parse csrf -> POST -> session
    cookie -> GET /feed-protected sequence, entirely through Anubis's
    own real challenge, yields the real protected posts."""
    output_path = tmp_path / "mock_target_login_protected_live.jsonl"

    items = run_spider_live("mock_target_login_protected.yaml", output_path, timeout=180)

    assert len(items) == EXPECTED_PROTECTED_PAGE_SIZE, (
        f"expected exactly {EXPECTED_PROTECTED_PAGE_SIZE} protected posts (a real login "
        f"succeeded and the resulting session was accepted) -- got {len(items)}"
    )
    for item in items:
        assert item["post_id"], f"post_id should not be empty: {item}"
        assert item["author"], f"author should not be empty: {item}"
    post_ids = [item["post_id"] for item in items]
    assert len(post_ids) == len(set(post_ids)), f"expected unique post_ids -- got {post_ids}"


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no live-network CI stack running)"
)
def test_feed_protected_without_any_login_yields_nothing_not_a_crash(tmp_path: Path) -> None:
    """The user's own explicit requirement: an attempt with no login
    configured at all against a real, session-gated target comes back a
    real, explicit 401 (test-environment/mock-target/app.py's own
    feed_protected route) -- logged clearly by GenericSpider
    (generic_spider.protected_target_rejected), not silently empty for
    no visible reason and not a crash. The crawl process itself must
    still exit cleanly (run_spider_live's own returncode==0 assertion)."""
    output_path = tmp_path / "mock_target_feed_protected_no_login_live.jsonl"

    items = run_spider_live("mock_target_feed_protected_no_login.yaml", output_path, timeout=180)

    assert items == [], (
        f"expected zero items -- /feed-protected requires a real session and none was "
        f"ever established -- got {len(items)}: a genuinely unauthenticated fetch should "
        f"never return real protected data"
    )


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no live-network CI stack running)"
)
def test_session_expired_mid_crawl_after_a_real_login_yields_nothing_not_a_crash(
    tmp_path: Path,
) -> None:
    """Session-expiry detection: a real login succeeds first (proving
    the session *was* genuinely valid at least once), then the
    deterministic test-only probe (LoginFlow.session_expiry_probe_url,
    see its own docstring) forces that exact session to be treated as
    already-expired before the real target is fetched -- the resulting
    401 must be logged clearly
    (camoufox_provider.session_expired_mid_crawl +
    generic_spider.protected_target_rejected), not silently empty and
    not a crash. Deliberately not a real, flaky multi-second TTL wait --
    see mock-target's own /test-expire-session route docstring for why."""
    output_path = tmp_path / "mock_target_login_protected_session_expiry_live.jsonl"

    items = run_spider_live(
        "mock_target_login_protected_session_expiry.yaml", output_path, timeout=180
    )

    assert items == [], (
        f"expected zero items -- the session was deliberately, deterministically expired "
        f"right after a real, successful login -- got {len(items)}: an expired session "
        f"should never return real protected data"
    )
