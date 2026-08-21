"""Unit tests for src/queue/tasks.py.

The subprocess runner is always injected: these tests never spawn a real
scrapy process.
"""

from __future__ import annotations

import subprocess

import pytest

from src.core.exceptions import QueueError
from src.queue.tasks import run_spider_job


def _completed(returncode: int, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout="", stderr=stderr)


def test_run_spider_job_returns_result_on_success() -> None:
    """Happy path: a zero exit code returns a result dict, no exception."""

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        assert "config_path=configs/target.yaml" in command
        return _completed(0)

    result = run_spider_job("configs/target.yaml", subprocess_runner=fake_runner)

    assert result == {"config_path": "configs/target.yaml", "returncode": 0}


def test_run_spider_job_raises_queue_error_on_nonzero_exit() -> None:
    """Failure case 1: a failed crawl raises QueueError with the real stderr, not silent."""

    def failing_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        return _completed(1, stderr="ConfigError: spider config not found")

    with pytest.raises(QueueError, match="ConfigError: spider config not found"):
        run_spider_job("configs/missing.yaml", subprocess_runner=failing_runner)


def test_run_spider_job_raises_queue_error_on_timeout() -> None:
    """Failure case 2: a hung crawl (timeout) is reported, not left to hang the worker."""

    def timing_out_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=command, timeout=600)

    with pytest.raises(QueueError, match="timed out"):
        run_spider_job("configs/target.yaml", subprocess_runner=timing_out_runner)


def test_run_spider_job_raises_queue_error_when_process_cannot_start() -> None:
    """Failure case 3: the subprocess itself failing to launch (e.g. missing interpreter)."""

    def broken_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        raise OSError("executable not found")

    with pytest.raises(QueueError, match="could not be started"):
        run_spider_job("configs/target.yaml", subprocess_runner=broken_runner)
