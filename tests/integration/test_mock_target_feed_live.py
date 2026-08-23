"""Integration test: src/spiders/configs/mock_target_feed.yaml against the
real, live test-environment/ stack -- the first real target for
GenericSpider's response_format: json path
(docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md's JSON/API round).
test-environment/mock-target's /api/feed (semi-GraphQL: nested
post -> comments JSON, ?after=<cursor> paging, README.md section 2.4) was
built in an earlier round but never wired up to GenericSpider until now.

Requires the same TITAN_BYPARR_URL-gated live-CI signal every other test
in this package uses. /api/feed sits behind Anubis exactly like `/` does,
so `antibot_provider: camoufox` (the one AntibotProvider confirmed to get
past Anubis's real challenge, docs/REQUIREMENTS.md section 9 entry 5) is
what this config uses.

**Round 1 of this path (CI run
[32656904590](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32656904590))
found a real, confirmed gap, not this test's own bug:** Camoufox got past
Anubis cleanly (a real 200), but `_default_camoufox_solve` read
`page.content()` -- the rendered DOM -- and Firefox (which Camoufox
drives) wraps any `application/json` response in its own built-in
plaintext viewer (`<html><body><pre>...` + `plaintext.css`) before that
DOM is ever read, confirmed by a diagnostic body-snippet log showing
exactly that wrapper. `response.json()` correctly refused to parse it.

**Fixed for real, not just theorized** (docs/REQUIREMENTS.md section 9
entry 9's follow-up): `_default_camoufox_solve` (and
`_default_patchright_solve`, applied on the same principle even though
Patchright never reaches a JSON endpoint in this stack, entry 7) now
reads the navigation response's raw network body
(`response.text()`) instead of `page.content()` specifically when the
response's own `content-type` header says `application/json` -- sidestepping
Firefox's/Chromium's built-in viewer entirely, since that raw body was
never touched by DOM rendering in the first place. Every other
content-type keeps using `page.content()` unchanged.
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
def test_mock_target_feed_yields_real_posts_from_the_json_api(tmp_path: Path) -> None:
    output_path = tmp_path / "mock_target_feed_live.jsonl"

    items = run_spider_live("mock_target_feed.yaml", output_path, timeout=180)

    assert len(items) > 0, (
        "expected real posts from /api/feed's JSON -- Camoufox's raw-network-body "
        "fix for JSON responses is confirmed to resolve the Firefox plaintext-viewer "
        "wrapping gap (see this test's module docstring); got 0 items -- something "
        "regressed"
    )
    first = items[0]
    assert first["post_id"], "post_id should not be empty"
    assert first["author"], "author should not be empty"
