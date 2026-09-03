"""Unit tests for src/strategy/strategy_registry.py.

Mirrors tests/unit/diagnostics/test_failure_registry.py's own structure
deliberately (see that module's own module docstring for why).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.diagnostics.failure_taxonomy import FailureCategory
from src.strategy.strategy_capability import StrategyCapability, StrategyMode
from src.strategy.strategy_decision import StrategyDecision
from src.strategy.strategy_registry import PATH_ENV_VAR, iter_decisions, record_decision


def _sample_decision(**overrides: object) -> StrategyDecision:
    defaults: dict[str, object] = {
        "timestamp": datetime(2026, 9, 3, 12, 0, 0, tzinfo=UTC),
        "target": "example.com",
        "capability": StrategyCapability.SWITCH_PROVIDER,
        "triggering_failure": FailureCategory.ANTIBOT_FINGERPRINT_REJECTION,
        "proposed_action": {"new_provider": "camoufox"},
        "mode_at_decision_time": StrategyMode.OBSERVE_ONLY,
        "enacted": False,
        "source": "test",
    }
    defaults.update(overrides)
    return StrategyDecision(**defaults)  # type: ignore[arg-type]


def test_record_decision_writes_one_jsonl_line_to_an_explicit_path(tmp_path: Path) -> None:
    target_file = tmp_path / "decisions.jsonl"

    record_decision(_sample_decision(), path=target_file)

    lines = target_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["target"] == "example.com"
    assert payload["capability"] == "switch-provider"
    assert payload["enacted"] is False
    assert payload["source"] == "test"


def test_record_decision_appends_not_overwrites(tmp_path: Path) -> None:
    target_file = tmp_path / "decisions.jsonl"

    record_decision(_sample_decision(target="a.example"), path=target_file)
    record_decision(_sample_decision(target="b.example"), path=target_file)

    lines = target_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["target"] == "a.example"
    assert json.loads(lines[1])["target"] == "b.example"


def test_record_decision_creates_parent_directories(tmp_path: Path) -> None:
    target_file = tmp_path / "nested" / "dir" / "decisions.jsonl"

    record_decision(_sample_decision(), path=target_file)

    assert target_file.exists()


def test_record_decision_uses_the_env_var_when_no_path_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target_file = tmp_path / "from_env.jsonl"
    monkeypatch.setenv(PATH_ENV_VAR, str(target_file))

    record_decision(_sample_decision())

    assert target_file.exists()


def test_record_decision_is_a_no_op_when_neither_path_nor_env_var_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(PATH_ENV_VAR, raising=False)
    monkeypatch.chdir(tmp_path)

    record_decision(_sample_decision())

    assert list(tmp_path.rglob("*.jsonl")) == []


def test_record_decision_swallows_a_write_error_and_logs_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    not_a_directory = tmp_path / "not_a_directory"
    not_a_directory.write_text("x", encoding="utf-8")
    impossible_path = not_a_directory / "decisions.jsonl"

    logged: list[tuple[str, dict[str, object]]] = []

    class _FakeLogger:
        def warning(self, msg: str, extra: dict[str, object] | None = None) -> None:
            logged.append((msg, extra or {}))

    monkeypatch.setattr("src.strategy.strategy_registry.get_logger", lambda name: _FakeLogger())

    record_decision(_sample_decision(), path=impossible_path)

    assert logged
    message, extra = logged[0]
    assert message == "strategy_registry.write_failed"
    assert extra["path"] == str(impossible_path)


def test_iter_decisions_reads_back_every_record_in_order(tmp_path: Path) -> None:
    target_file = tmp_path / "decisions.jsonl"
    record_decision(_sample_decision(target="a.example"), path=target_file)
    record_decision(_sample_decision(target="b.example"), path=target_file)

    records = list(iter_decisions(target_file))

    assert [r.target for r in records] == ["a.example", "b.example"]
    assert all(isinstance(r, StrategyDecision) for r in records)


def test_iter_decisions_skips_blank_lines(tmp_path: Path) -> None:
    target_file = tmp_path / "decisions.jsonl"
    record_decision(_sample_decision(), path=target_file)
    with target_file.open("a", encoding="utf-8") as handle:
        handle.write("\n")

    records = list(iter_decisions(target_file))

    assert len(records) == 1


def test_iter_decisions_raises_on_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        list(iter_decisions(tmp_path / "does_not_exist.jsonl"))
