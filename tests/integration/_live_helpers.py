"""Shared subprocess helper for the live Test Targets integration tests.

Every target-specific test in this package runs the real
``generic_spider.py`` against a real, live site via ``scrapy runspider``
in a subprocess (same reasoning as ``test_playwright_live_render.py``:
Twisted's reactor can only be installed once per process). This module
only factors out that repeated subprocess/JSONL-parsing boilerplate --
it is not itself a test module (no ``test_*`` name, not collected by
pytest) and does not change any spider behaviour.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGS_DIR = REPO_ROOT / "src" / "spiders" / "configs"
GENERIC_SPIDER = REPO_ROOT / "src" / "spiders" / "generic_spider.py"


def run_spider_live(
    config_name: str,
    output_path: Path,
    timeout: int = 150,
    extra_settings: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Run ``config_name`` (a file under ``src/spiders/configs/``) for real.

    Returns the parsed JSON-lines items. Raises ``AssertionError`` (via a
    plain ``assert``) with the real stderr tail if the crawl process
    itself failed -- callers still do their own assertions on the
    returned items.
    """
    config_path = CONFIGS_DIR / config_name
    cmd = [
        sys.executable,
        "-m",
        "scrapy",
        "runspider",
        str(GENERIC_SPIDER),
        "-a",
        f"config_path={config_path}",
        "-s",
        "LOG_LEVEL=WARNING",
        "-o",
        str(output_path),
    ]
    for key, value in (extra_settings or {}).items():
        cmd.extend(["-s", f"{key}={value}"])

    result = subprocess.run(  # noqa: S603
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert result.returncode == 0, f"scrapy runspider failed:\n{result.stderr[-4000:]}"

    if not output_path.exists():
        return []
    lines = output_path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines if line]
