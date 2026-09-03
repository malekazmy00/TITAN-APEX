"""Integration test: Playwright must actually render JS-driven content.

Requires real, unrestricted outbound internet for a real headless-browser
navigation to a real external site -- this is deliberately NOT a unit
test, and is expected to fail (or hang) in a sandboxed dev environment
without full network access to arbitrary hosts. It runs for real in CI
(see .github/workflows/ci.yml), which has unrestricted internet access.

docs/REQUIREMENTS.md section 9 entry 30.2's own honest diagnostic-gap
finding: this test used to assert only ``result.returncode`` and never
printed the subprocess's own stderr on an *item-count* assertion
failure (only `_live_helpers.py`'s ``run_spider_live`` did that, via its
own unconditional print -- this file built its own subprocess call
directly and never had it). That gap meant two real, CI-confirmed
failures of this exact test (entry 30.2's own 4-data-point trail) had to
be classified ``external-site-flake`` without being able to rule out a
genuine recurrence of entries 25/27's own timing-race class
(``requests_during_scroll: 0``) from the CI log alone.  The fix below is
exactly `_live_helpers.py`'s own pattern, so any future occurrence is
fully diagnosable from the CI log on the first try.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "src" / "spiders" / "configs" / "scrapingcourse_infinite_scrolling.yaml"

# The target's static HTML alone already contains this many products;
# JS-triggered infinite scroll must load at least one more batch.
STATIC_BATCH_SIZE = 12


def test_infinite_scrolling_target_yields_more_than_the_static_batch(tmp_path: Path) -> None:
    output_path = tmp_path / "infinite_scrolling_live.jsonl"

    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "scrapy",
            "runspider",
            str(REPO_ROOT / "src" / "spiders" / "generic_spider.py"),
            "-a",
            f"config_path={CONFIG_PATH}",
            "-s",
            "LOG_LEVEL=WARNING",
            "-o",
            str(output_path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=150,
    )

    # Printed unconditionally (not just on a non-zero returncode) -- same
    # pattern as _live_helpers.py's own run_spider_live, added after
    # entry 30.2's own honest diagnostic-gap finding (this module's own
    # docstring has the full reasoning): pytest only surfaces captured
    # stdout on a *failing* test, so this is a no-op for a passing one,
    # but gives real, immediate diagnostic evidence (this test's own
    # playwright_middleware.scroll_diagnostics log line --
    # requests_during_scroll in particular) the next time the item-count
    # assertion below fails, instead of an undiagnosable gap.
    print(f"--- scrapy runspider stderr tail (infinite_scrolling) ---\n{result.stderr[-4000:]}")
    assert result.returncode == 0, f"scrapy runspider failed:\n{result.stderr[-4000:]}"

    lines = (
        output_path.read_text(encoding="utf-8").strip().splitlines() if output_path.exists() else []
    )
    assert len(lines) > STATIC_BATCH_SIZE, (
        f"expected more than {STATIC_BATCH_SIZE} items (the static page alone already has "
        f"{STATIC_BATCH_SIZE}); got {len(lines)} -- JS-triggered infinite scroll did not add more"
    )
