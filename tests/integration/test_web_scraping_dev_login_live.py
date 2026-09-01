"""Integration test: web-scraping.dev's real login/session flow, via
both browser-driving providers (Level 3,
docs/ADVANCED_TEST_TARGETS_L3.md) -- the first real test of
docs/REQUIREMENTS.md section 9 entry 21's LoginFlow machinery against a
genuine external target, outside this project's own mock-target.

Real credentials confirmed by hand (curl against /credentials, itself
Referer-gated -- see
src/spiders/configs/web_scraping_dev_login_camoufox.yaml's own
comments for the full trail): username ``user123``, password
``password``. A real POST to /api/login with these returns
``Set-Cookie: auth=...``, and a follow-up GET to /login with that
cookie shows real protected content ("Logged in as User123 ... The
secret message is: 🤫").

Two separate tests, one per browser-driving provider (Byparr is
structurally excluded -- SpiderConfig's own validator requires
camoufox/patchright for ``login``, since only a real, live browser page
can fill and submit a real DOM form at all) -- each is its own,
independently readable pass/fail signal for that provider's LoginFlow
support against a real target, not just this project's own
mock-target.
"""

from __future__ import annotations

from pathlib import Path

from tests.integration._live_helpers import run_spider_live


def _assert_logged_in(items: list[dict[str, object]], provider_name: str) -> None:
    assert len(items) == 1, (
        f"[{provider_name}] expected exactly 1 status item from the post-login page, "
        f"got {len(items)}"
    )
    logged_in_as = items[0]["logged_in_as"]
    assert logged_in_as and "User123" in str(logged_in_as), (
        f"[{provider_name}] expected 'Logged in as User123' -- got {logged_in_as!r}: "
        f"the login POST (username=user123/password=password) never actually "
        f"authenticated the session"
    )
    secret_message = items[0]["secret_message"]
    assert secret_message, (
        f"[{provider_name}] expected a real secret message on the authenticated page, "
        f"got {secret_message!r} -- the session cookie may not have carried over to "
        f"this read"
    )


def test_web_scraping_dev_login_via_camoufox(tmp_path: Path) -> None:
    output_path = tmp_path / "web_scraping_dev_login_camoufox_live.jsonl"

    items = run_spider_live("web_scraping_dev_login_camoufox.yaml", output_path)

    _assert_logged_in(items, "camoufox")


def test_web_scraping_dev_login_via_patchright(tmp_path: Path) -> None:
    output_path = tmp_path / "web_scraping_dev_login_patchright_live.jsonl"

    items = run_spider_live("web_scraping_dev_login_patchright.yaml", output_path)

    _assert_logged_in(items, "patchright")
