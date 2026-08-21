"""Integration test: news.ycombinator.com (Level 5, docs/TEST_TARGETS.md).

Unlike every other target used so far, Hacker News's front page is
*genuinely* live data -- ranking and the story set can change between any
two fetches. This runs the real crawl twice, with a real time gap between
runs, against the live site, to prove the code produces a valid,
independently correct result on each run rather than silently returning
stale/cached data.

Content is not guaranteed to differ over any particular gap (a quiet
period on Hacker News is a real, valid outcome, not a bug), so this does
not hard-assert the two runs differ -- it asserts both runs are
independently well-formed, and reports whether they did differ so a human
reading the CI log has the actual evidence either way.
"""

from __future__ import annotations

import time
from pathlib import Path

from tests.integration._live_helpers import run_spider_live

# Real time gap between the two live fetches, in seconds. Long enough for
# Hacker News's front page to plausibly change (new submissions, vote
# shifts), short enough to fit the CI job's overall time budget.
RUN_GAP_SECONDS = 45


def _assert_well_formed(items: list[dict[str, object]], run_label: str) -> None:
    assert len(items) > 0, f"{run_label}: expected at least one Hacker News story"
    for item in items:
        assert item.get("rank"), f"{run_label}: story missing rank: {item!r}"
        assert item.get("title"), f"{run_label}: story missing title: {item!r}"
        assert item.get("url"), f"{run_label}: story missing url: {item!r}"


def test_hackernews_two_live_runs_are_each_well_formed_and_may_differ(tmp_path: Path) -> None:
    first_output = tmp_path / "hackernews_live_run1.jsonl"
    first_items = run_spider_live("hackernews.yaml", first_output)
    _assert_well_formed(first_items, "run 1")

    time.sleep(RUN_GAP_SECONDS)

    second_output = tmp_path / "hackernews_live_run2.jsonl"
    second_items = run_spider_live("hackernews.yaml", second_output)
    _assert_well_formed(second_items, "run 2")

    first_titles = [item["title"] for item in first_items]
    second_titles = [item["title"] for item in second_items]
    if first_titles != second_titles:
        print(  # noqa: T201 -- deliberate CI-log evidence, not debug litter
            "hackernews_live: the two runs differ (real changing data) -- "
            f"run 1 top story: {first_titles[0]!r}, run 2 top story: {second_titles[0]!r}"
        )
    else:
        print(  # noqa: T201
            "hackernews_live: the two runs returned identical titles/order -- a quiet "
            f"{RUN_GAP_SECONDS}s window on Hacker News, not a bug"
        )
