from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.diagnostics.failure_taxonomy import FailureCategory
from src.strategy.strategy_capability import StrategyCapability, StrategyMode
from src.strategy.strategy_decision import StrategyDecision


def test_happy_path_with_every_field_set() -> None:
    decision = StrategyDecision(
        timestamp=datetime(2026, 9, 3, 12, 0, 0, tzinfo=UTC),
        target="example.com",
        capability=StrategyCapability.SWITCH_PROVIDER,
        triggering_failure=FailureCategory.ANTIBOT_FINGERPRINT_REJECTION,
        proposed_action={"current_provider": "byparr", "new_provider": "camoufox"},
        mode_at_decision_time=StrategyMode.ENACT,
        enacted=True,
        source="strategy_engine.switch_provider",
    )

    assert decision.enacted is True
    assert decision.proposed_action["new_provider"] == "camoufox"


def test_proposed_action_defaults_to_empty_dict() -> None:
    decision = StrategyDecision(
        timestamp=datetime(2026, 9, 3, 12, 0, 0, tzinfo=UTC),
        target="example.com",
        capability=StrategyCapability.ADJUST_BACKOFF,
        triggering_failure=FailureCategory.NETWORK_INFRA_TRANSIENT,
        mode_at_decision_time=StrategyMode.OBSERVE_ONLY,
        enacted=False,
        source="test",
    )

    assert decision.proposed_action == {}


def test_requires_source() -> None:
    with pytest.raises(ValidationError):
        StrategyDecision(  # type: ignore[call-arg]
            timestamp=datetime(2026, 9, 3, 12, 0, 0, tzinfo=UTC),
            target="example.com",
            capability=StrategyCapability.ADJUST_BACKOFF,
            triggering_failure=FailureCategory.NETWORK_INFRA_TRANSIENT,
            mode_at_decision_time=StrategyMode.OBSERVE_ONLY,
            enacted=False,
        )
