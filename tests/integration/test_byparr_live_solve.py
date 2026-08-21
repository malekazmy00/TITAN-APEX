"""Integration test: Byparr must actually solve a real anti-bot challenge.

Requires a running Byparr instance (TITAN_BYPARR_URL) with real,
unrestricted outbound internet. Skips cleanly (not a failure) if
TITAN_BYPARR_URL isn't set -- e.g. local dev without
`docker compose up byparr`. It runs for real in CI, where the byparr
service (.github/workflows/ci.yml) is always up.
"""

from __future__ import annotations

import os

import pytest

from src.providers.antibot.byparr_provider import ByparrProvider

BYPARR_URL = os.environ.get("TITAN_BYPARR_URL")


@pytest.mark.skipif(
    not BYPARR_URL, reason="TITAN_BYPARR_URL not set (no Byparr instance running)"
)
def test_byparr_solves_a_real_antibot_challenge() -> None:
    assert BYPARR_URL  # guarded by skipif above; narrows type for mypy too
    provider = ByparrProvider(base_url=BYPARR_URL, timeout_ms=90_000)

    solution = provider.solve("https://www.scrapingcourse.com/antibot-challenge")

    assert solution.status_code == 200
    assert len(solution.html) > 500
