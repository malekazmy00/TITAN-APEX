"""Unit tests for mock-target/security/file_logger.py."""

from __future__ import annotations

import json
from pathlib import Path

from security.file_logger import get_file_logger


def test_logs_one_json_line_per_call(tmp_path: Path) -> None:
    """Happy path: each log call appends exactly one parseable JSON line."""
    log_path = tmp_path / "events.log"
    logger = get_file_logger("test.happy", str(log_path))

    logger.critical("something.happened", extra={"token": "abc"})

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["level"] == "CRITICAL"
    assert payload["message"] == "something.happened"
    assert payload["token"] == "abc"


def test_repeated_calls_do_not_duplicate_handlers(tmp_path: Path) -> None:
    """Failure case 1: calling get_file_logger again for the same name must not
    attach a second handler, or every log line would be written twice."""
    log_path = tmp_path / "events.log"
    logger1 = get_file_logger("test.no_dup", str(log_path))
    logger2 = get_file_logger("test.no_dup", str(log_path))

    logger2.info("one.event")

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert logger1 is logger2


def test_non_json_safe_values_fall_back_to_repr(tmp_path: Path) -> None:
    """Failure case 2: an unserializable extra value must not crash logging --
    it degrades to repr() instead."""
    log_path = tmp_path / "events.log"
    logger = get_file_logger("test.unsafe_value", str(log_path))

    logger.warning("weird.value", extra={"thing": {1, 2, 3}})

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    payload = json.loads(lines[0])
    assert "thing" in payload


def test_creates_parent_directories_that_do_not_exist_yet(tmp_path: Path) -> None:
    nested_path = tmp_path / "a" / "b" / "c" / "events.log"
    logger = get_file_logger("test.nested_dir", str(nested_path))

    logger.info("created")

    assert nested_path.exists()


def test_same_name_different_path_reconfigures_instead_of_keeping_the_stale_file(
    tmp_path: Path,
) -> None:
    """A real scenario in this app's own test suite: several MockTargetConfig
    instances with different tmp_path log files, but the same fixed logger
    name (create_app() always calls get_file_logger with the same name).
    The second config's events must land in *its* file, not the first's."""
    first_path = tmp_path / "first.log"
    second_path = tmp_path / "second.log"

    get_file_logger("test.reconfigure", str(first_path)).info("first")
    get_file_logger("test.reconfigure", str(second_path)).info("second")

    assert "first" in first_path.read_text(encoding="utf-8")
    assert "second" in second_path.read_text(encoding="utf-8")
    assert "second" not in first_path.read_text(encoding="utf-8")
