"""Integration test: Redis + RQ must actually enqueue and run a real crawl job.

Requires a running Redis instance (TITAN_REDIS_URL). Skips cleanly (not a
failure) if TITAN_REDIS_URL isn't set -- e.g. local dev without a running
Redis. Runs for real in CI, where the redis service
(.github/workflows/ci.yml) is always up.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.queue.enqueue import enqueue_crawl

REDIS_URL = os.environ.get("TITAN_REDIS_URL")
REPO_ROOT = Path(__file__).resolve().parents[2]
RQ_CLI = str(Path(sys.executable).parent / "rq")


@pytest.mark.skipif(not REDIS_URL, reason="TITAN_REDIS_URL not set (no Redis instance running)")
def test_enqueued_crawl_job_runs_for_real_via_an_rq_worker(tmp_path: Path) -> None:
    db_path = tmp_path / "redis_queue_live.sqlite3"
    config_path = REPO_ROOT / "src" / "spiders" / "configs" / "quotes_toscrape.yaml"

    job_id = enqueue_crawl(str(config_path))
    assert job_id

    env = {**os.environ, "TITAN_STORAGE_PATH": str(db_path)}
    result = subprocess.run(  # noqa: S603
        [RQ_CLI, "worker", "crawls", "--burst"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    combined_output = result.stdout + result.stderr

    assert result.returncode == 0, f"rq worker failed:\n{combined_output[-4000:]}"
    assert "Job OK" in combined_output, f"job did not report success:\n{combined_output[-4000:]}"
    assert db_path.exists(), "the crawl job ran but never wrote to storage"
