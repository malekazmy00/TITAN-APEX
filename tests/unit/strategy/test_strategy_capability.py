from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.strategy.strategy_capability import StrategyCapability, StrategyEngineConfig, StrategyMode


def test_default_config_is_fully_disabled() -> None:
    """The master switch, and every capability's own mode, default to
    the safest state -- zero behavior change anywhere else unless
    explicitly opted into."""
    config = StrategyEngineConfig()

    assert config.engine_enabled is False
    assert config.switch_provider_mode is StrategyMode.OBSERVE_ONLY
    assert config.adjust_backoff_mode is StrategyMode.OBSERVE_ONLY
    assert config.target_new_urls_mode is StrategyMode.DISABLED_NOT_IMPLEMENTED


def test_adjust_backoff_max_multiplier_bounds() -> None:
    with pytest.raises(ValidationError):
        StrategyEngineConfig(adjust_backoff_max_multiplier=1.0)  # not > 1.0
    with pytest.raises(ValidationError):
        StrategyEngineConfig(adjust_backoff_max_multiplier=5.1)  # exceeds the ceiling

    config = StrategyEngineConfig(adjust_backoff_max_multiplier=5.0)
    assert config.adjust_backoff_max_multiplier == 5.0


def test_target_new_urls_mode_rejects_observe_only() -> None:
    """No executor exists for TARGET_NEW_URLS -- even OBSERVE_ONLY (the
    otherwise-always-safe default) must be refused, loudly, not silently
    accepted as a no-op."""
    with pytest.raises(ValidationError, match="DISABLED_NOT_IMPLEMENTED"):
        StrategyEngineConfig(target_new_urls_mode=StrategyMode.OBSERVE_ONLY)


def test_target_new_urls_mode_rejects_enact() -> None:
    with pytest.raises(ValidationError, match="DISABLED_NOT_IMPLEMENTED"):
        StrategyEngineConfig(target_new_urls_mode=StrategyMode.ENACT)


def test_capability_and_mode_values() -> None:
    assert {member.value for member in StrategyCapability} == {
        "switch-provider",
        "adjust-backoff",
        "target-new-urls",
    }
    assert {member.value for member in StrategyMode} == {
        "observe-only",
        "enact",
        "disabled-not-implemented",
    }


def test_switch_provider_after_n_challenges_must_be_at_least_one() -> None:
    with pytest.raises(ValidationError):
        StrategyEngineConfig(switch_provider_after_n_challenges=0)
