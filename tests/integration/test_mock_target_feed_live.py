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

**Confirmed for real (CI run
[32656904590](https://github.com/malekazmy00/TITAN-APEX/actions/runs/32656904590)):
this does NOT work -- and the real root cause is exactly the hypothesis
raised while building this round, confirmed by a diagnostic log, not
assumed.** `camoufox_provider.solved` shows a clean 200 past Anubis
(html_length 7433, real Anubis auth cookies present), but
`generic_spider.invalid_json` fired right after with this actual
response body (first ~300 chars, captured in CI):

```
<html><head><link rel="stylesheet" href="resource://content-accessible/plaintext.css"></head><body><pre>{"edges":[{"comments":[{"author":"delgadotiffany",...
```

Camoufox drives a real Firefox engine, and Firefox wraps any
`application/json` (or otherwise non-HTML-typed) response in its own
built-in plaintext viewer -- an `<html><body><pre>...</pre></body></html>`
shell around the real JSON -- before `page.content()` (what
`CamoufoxProvider` hands back) ever gets read. `response.json()`'s plain
`json.loads(response.text)` sees that HTML shell, not raw JSON, and
correctly refuses to parse it (GenericSpider's own error handling worked
exactly as designed here -- this is an environment/provider gap, not a
bug in the JSON-parsing code itself).

**Not fixed this round, per this project's own rule: document the real
gap, don't force a fix without it being the actual point of the round.**
The real JSON is still recoverable (it's sitting inside that `<pre>` tag
verbatim), and Playwright's own navigation `Response.text()` (the raw
network body, captured independently of DOM rendering) would sidestep
this entirely -- a concrete, real fix direction for a future round, not
implemented here. This assertion is a regression sentinel, matching the
same convention `test_mock_target_live.py`/`test_mock_target_patchright_live.py`
already established for their own confirmed gaps.
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
def test_mock_target_feed_yields_zero_items_firefox_json_viewer_wraps_the_body(
    tmp_path: Path,
) -> None:
    """Documents the real, confirmed outcome (see module docstring for the
    full, evidenced root cause: Camoufox/Firefox's own plaintext-viewer
    HTML wrapper around a raw JSON response body)."""
    output_path = tmp_path / "mock_target_feed_live.jsonl"

    items = run_spider_live("mock_target_feed.yaml", output_path, timeout=180)

    assert items == [], (
        f"expected zero items (see this test's module docstring for the "
        f"real, evidenced reason -- Firefox's plaintext-viewer HTML wrapper "
        f"around the raw JSON response); got {len(items)}: {items[:1]}"
    )
