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

**Not yet confirmed either way** -- this is the first real run of the
JSON-parsing path against a real browser-rendered response. Whether
Firefox's own built-in JSON viewer (Camoufox drives a real Firefox
engine) transforms the rendered DOM enough to break `response.json()`'s
plain `json.loads(response.text)` is a real, open question, not assumed
answered by unit tests that build a JSON response by hand. Once this has
run for real in CI, this docstring (and docs/REQUIREMENTS.md) get updated
with the real, evidenced result either way.
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
        "expected real posts from /api/feed's JSON -- got 0 items; see this "
        "test's module docstring: this is the first real test of this path, "
        "not yet confirmed either way"
    )
    first = items[0]
    assert first["post_id"], "post_id should not be empty"
    assert first["author"], "author should not be empty"
