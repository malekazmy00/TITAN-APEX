"""Unit tests for src/diagnostics/failure_registry.py.

No real project file is ever written here -- every test uses an
explicit ``tmp_path`` destination (or deliberately gives neither a path
nor the env var, to prove the no-op default holds).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.diagnostics.failure_registry import PATH_ENV_VAR, iter_failures, record_failure
from src.diagnostics.failure_taxonomy import FailureCategory, FailureRecord, ResolutionStatus


def _sample_record(**overrides: object) -> FailureRecord:
    defaults: dict[str, object] = {
        "timestamp": datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC),
        "target": "https://example.com/",
        "provider": "camoufox",
        "failure_category": FailureCategory.TIMING_RACE,
        "raw_signal": {"requests_during_scroll": 0},
        "resolution_status": ResolutionStatus.UNRESOLVED,
        "source": "test",
    }
    defaults.update(overrides)
    return FailureRecord(**defaults)  # type: ignore[arg-type]


def test_record_failure_writes_one_jsonl_line_to_an_explicit_path(tmp_path: Path) -> None:
    """Happy path: an explicit path is always honored, regardless of the
    environment variable."""
    target_file = tmp_path / "failures.jsonl"
    record = _sample_record()

    record_failure(record, path=target_file)

    lines = target_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["target"] == "https://example.com/"
    assert payload["provider"] == "camoufox"
    assert payload["failure_category"] == "timing-race"
    assert payload["raw_signal"] == {"requests_during_scroll": 0}
    assert payload["source"] == "test"


def test_record_failure_appends_not_overwrites(tmp_path: Path) -> None:
    target_file = tmp_path / "failures.jsonl"

    record_failure(_sample_record(target="https://a.example/"), path=target_file)
    record_failure(_sample_record(target="https://b.example/"), path=target_file)

    lines = target_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["target"] == "https://a.example/"
    assert json.loads(lines[1])["target"] == "https://b.example/"


def test_record_failure_creates_parent_directories(tmp_path: Path) -> None:
    target_file = tmp_path / "nested" / "dir" / "failures.jsonl"

    record_failure(_sample_record(), path=target_file)

    assert target_file.exists()


def test_record_failure_uses_the_env_var_when_no_path_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target_file = tmp_path / "from_env.jsonl"
    monkeypatch.setenv(PATH_ENV_VAR, str(target_file))

    record_failure(_sample_record())

    assert target_file.exists()


def test_record_failure_is_a_no_op_when_neither_path_nor_env_var_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure-adjacent case, and the whole point of this module's own
    safety design (see its docstring): every pre-existing unit test in
    this project that triggers a real failure path (a circuit breaker
    opening, a provider raising AntibotError, ...) must never write a
    stray row into the real project history file just by running."""
    monkeypatch.delenv(PATH_ENV_VAR, raising=False)
    monkeypatch.chdir(tmp_path)

    record_failure(_sample_record())

    assert list(tmp_path.rglob("*.jsonl")) == []


def test_record_failure_swallows_a_write_error_and_logs_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure case: a destination that can't actually be written to
    (here, a path whose 'parent' is a file, not a directory) must not
    raise -- recording a failure must never itself crash the crawl that
    triggered it."""
    not_a_directory = tmp_path / "not_a_directory"
    not_a_directory.write_text("x", encoding="utf-8")
    impossible_path = not_a_directory / "failures.jsonl"

    logged: list[tuple[str, dict[str, object]]] = []

    class _FakeLogger:
        def warning(self, msg: str, extra: dict[str, object] | None = None) -> None:
            logged.append((msg, extra or {}))

    monkeypatch.setattr(
        "src.diagnostics.failure_registry.get_logger", lambda name: _FakeLogger()
    )

    record_failure(_sample_record(), path=impossible_path)

    assert logged
    message, extra = logged[0]
    assert message == "failure_registry.write_failed"
    assert extra["path"] == str(impossible_path)


def test_iter_failures_reads_back_every_record_in_order(tmp_path: Path) -> None:
    target_file = tmp_path / "failures.jsonl"
    record_failure(_sample_record(target="https://a.example/"), path=target_file)
    record_failure(_sample_record(target="https://b.example/"), path=target_file)

    records = list(iter_failures(target_file))

    assert [r.target for r in records] == ["https://a.example/", "https://b.example/"]
    assert all(isinstance(r, FailureRecord) for r in records)


def test_iter_failures_skips_blank_lines(tmp_path: Path) -> None:
    target_file = tmp_path / "failures.jsonl"
    record_failure(_sample_record(), path=target_file)
    with target_file.open("a", encoding="utf-8") as handle:
        handle.write("\n")

    records = list(iter_failures(target_file))

    assert len(records) == 1


def test_iter_failures_raises_on_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        list(iter_failures(tmp_path / "does_not_exist.jsonl"))
