"""Integration test: src/spiders/configs/mock_target_live_dom.yaml against
the real, live test-environment/ stack -- the real, architectural fix
(docs/REQUIREMENTS.md section 9 entry 12) for entry 11's confirmed Shadow
DOM gap (test_mock_target_shadow_dom_live.py), no external library.

Same target/challenge chain as mock_target_camoufox.yaml/entry 11
(Anubis, the cookie wall, A/B variants, markup randomizer, honeypots, and
Shadow DOM -- all active at once, entry 10's combined round) -- the only
difference is `extraction_mode: live_dom`: CamoufoxProvider extracts
items directly from its own live browser page (Playwright's
``page.locator()``, which auto-pierces *open* shadow roots) before
closing it, instead of returning ``page.content()`` for GenericSpider to
re-parse as a string later. A shadow root attached via
``Element.attachShadow()`` is never included in that serialized string at
all, regardless of which browser produced it -- entry 11's real,
confirmed root cause; this is the real fix for it, not a workaround.

Requires the same TITAN_BYPARR_URL-gated live-CI signal every other test
in this package uses.
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
def test_mock_target_live_dom_recovers_every_shadow_dom_wrapped_post(tmp_path: Path) -> None:
    """Regression sentinel for the fix, mirroring
    test_mock_target_shadow_dom_live.py's own regression-sentinel shape
    for the gap: `app.py`'s `INDEX_PAGE_SIZE` (10) and
    `structural/shadow_dom.py`'s `is_shadow_wrapped` (every odd 0-based
    index) are both deterministic, so this crawl's exact item count is a
    precise, reproducible number -- **11, not 6**: all 10 real posts (5
    light-DOM + 5 recovered from inside a shadow root) plus the 1 decoy
    twin (`structural/decoy_data.py`, always light DOM, unaffected by
    this layer) -- matching the pre-Shadow-DOM baseline
    (`test-environment/tests/test_app.py::test_index_renders_posts_decoy_and_honeypots`'s
    own `body.count('data-role="post"') == 11`) exactly, because live-DOM
    extraction genuinely recovers every post the parsed_html path
    (entry 11) missed.

    If this ever regresses (fewer than 11 items), that's real evidence
    the live-DOM extraction path stopped reaching the shadow-wrapped
    posts -- this test should be re-diagnosed from real CI logs, not
    silently loosened to match a smaller number.
    """
    output_path = tmp_path / "mock_target_live_dom_live.jsonl"

    items = run_spider_live("mock_target_live_dom.yaml", output_path, timeout=180)

    assert len(items) == 11, (
        f"expected exactly 11 items (10 real posts, 5 of them recovered "
        f"from inside a shadow root, + 1 decoy twin) -- got {len(items)}: "
        f"see this module's docstring for why 11 is the real, deterministic "
        f"expectation, not an approximation"
    )
    # Every item must carry real, non-empty content -- not just be present
    # as an empty placeholder (e.g. if text_content() on a shadow-root
    # element somehow came back blank instead of genuinely extracted).
    for item in items:
        assert item["post_id"], f"post_id should not be empty: {item}"
        assert item["author"], f"author should not be empty: {item}"
        assert item["text"], f"text should not be empty: {item}"

    # post_ids must be unique except for the one deliberate exception:
    # the decoy twin shares its post_id with the real post it stole
    # (structural/decoy_data.py's own contract) -- so exactly one
    # duplicate is expected, not zero and not more than one.
    post_ids = [item["post_id"] for item in items]
    duplicate_count = len(post_ids) - len(set(post_ids))
    assert duplicate_count == 1, (
        f"expected exactly one duplicate post_id (the decoy twin) -- got "
        f"{duplicate_count} duplicates among {len(post_ids)} items"
    )
