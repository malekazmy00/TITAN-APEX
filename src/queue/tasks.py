"""RQ task: runs one spider crawl for a target config in a subprocess.

A subprocess (not an in-process CrawlerProcess) is used deliberately:
Twisted's reactor can only be installed once per process, so a long-lived
RQ worker that processed more than one crawl job in-process would crash
on the second job. Each job gets a clean process instead — the same
pattern already used by tests/integration/test_playwright_live_render.py.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.core.exceptions import QueueError

REPO_ROOT = Path(__file__).resolve().parents[2]

SubprocessRunner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def _default_subprocess_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        command, cwd=REPO_ROOT, capture_output=True, text=True, timeout=600
    )


def run_spider_job(
    config_path: str,
    subprocess_runner: SubprocessRunner | None = None,
) -> dict[str, Any]:
    """Run ``generic_spider.py`` against ``config_path`` in a subprocess.

    This is the function RQ workers execute for a queued crawl job.

    Raises:
        QueueError: if the crawl process fails to start, times out, or
            exits with a non-zero status — so RQ correctly marks the job
            as failed instead of silently reporting success.
    """
    runner = subprocess_runner or _default_subprocess_runner
    command = [
        sys.executable,
        "-m",
        "scrapy",
        "runspider",
        str(REPO_ROOT / "src" / "spiders" / "generic_spider.py"),
        "-a",
        f"config_path={config_path}",
        "-s",
        "LOG_LEVEL=WARNING",
    ]

    try:
        result = runner(command)
    except subprocess.TimeoutExpired as exc:
        raise QueueError(f"crawl job timed out for {config_path}: {exc}") from exc
    except OSError as exc:
        raise QueueError(f"crawl job could not be started for {config_path}: {exc}") from exc

    if result.returncode != 0:
        raise QueueError(
            f"crawl job failed for {config_path} (exit {result.returncode}): "
            f"{result.stderr[-2000:]}"
        )

    return {"config_path": config_path, "returncode": result.returncode}
