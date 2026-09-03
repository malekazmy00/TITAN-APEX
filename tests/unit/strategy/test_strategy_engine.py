from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.diagnostics.failure_taxonomy import FailureCategory
from src.strategy.strategy_capability import StrategyEngineConfig, StrategyMode, TargetPolicyStatus
from src.strategy.strategy_engine import PROVIDER_ROTATION, StrategyEngine, _next_in_rotation


def _fixed_now() -> datetime:
    return datetime(2026, 9, 3, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _isolate_decision_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No test here should ever write to a real project file -- mirrors
    every other diagnostics/strategy test's own isolation."""
    monkeypatch.delenv("TITAN_STRATEGY_DECISION_LOG_PATH", raising=False)
    monkeypatch.chdir(tmp_path)


class TestNextInRotation:
    def test_wraps_around_from_the_last_provider(self) -> None:
        assert _next_in_rotation(PROVIDER_ROTATION[-1]) == PROVIDER_ROTATION[0]

    def test_advances_to_the_following_provider(self) -> None:
        assert _next_in_rotation(PROVIDER_ROTATION[0]) == PROVIDER_ROTATION[1]

    def test_unrecognized_provider_falls_back_to_the_first(self) -> None:
        assert _next_in_rotation("not-a-real-provider") == PROVIDER_ROTATION[0]


class TestDecideSwitchProvider:
    def test_disabled_engine_returns_none(self) -> None:
        engine = StrategyEngine(StrategyEngineConfig(engine_enabled=False), now_fn=_fixed_now)

        result = engine.decide_switch_provider(
            domain="example.com",
            request_url="https://example.com/",
            current_provider="byparr",
            triggering_failure=FailureCategory.ANTIBOT_FINGERPRINT_REJECTION,
        )

        assert result is None

    def test_below_threshold_returns_none(self) -> None:
        config = StrategyEngineConfig(engine_enabled=True, switch_provider_after_n_challenges=3)
        engine = StrategyEngine(config, now_fn=_fixed_now)

        result = engine.decide_switch_provider(
            domain="example.com",
            request_url="https://example.com/",
            current_provider="byparr",
            triggering_failure=FailureCategory.ANTIBOT_FINGERPRINT_REJECTION,
        )

        assert result is None

    def test_reaching_the_threshold_proposes_the_next_provider_in_observe_only(self) -> None:
        config = StrategyEngineConfig(
            engine_enabled=True,
            switch_provider_mode=StrategyMode.OBSERVE_ONLY,
            switch_provider_after_n_challenges=2,
        )
        engine = StrategyEngine(config, now_fn=_fixed_now)

        first = engine.decide_switch_provider(
            domain="example.com",
            request_url="https://example.com/",
            current_provider="byparr",
            triggering_failure=FailureCategory.ANTIBOT_FINGERPRINT_REJECTION,
        )
        assert first is None  # 1st failure -- streak == 1, below threshold 2

        second = engine.decide_switch_provider(
            domain="example.com",
            request_url="https://example.com/",
            current_provider="byparr",
            triggering_failure=FailureCategory.ANTIBOT_FINGERPRINT_REJECTION,
        )

        assert second is not None
        assert second.enacted is False
        assert second.proposed_action["current_provider"] == "byparr"
        assert second.proposed_action["new_provider"] == "camoufox"
        assert second.proposed_action["streak"] == 2

    def test_enact_mode_marks_the_decision_enacted(self) -> None:
        config = StrategyEngineConfig(
            engine_enabled=True,
            switch_provider_mode=StrategyMode.ENACT,
            switch_provider_after_n_challenges=1,
        )
        engine = StrategyEngine(config, now_fn=_fixed_now)

        result = engine.decide_switch_provider(
            domain="example.com",
            request_url="https://example.com/",
            current_provider="byparr",
            triggering_failure=FailureCategory.ANTIBOT_FINGERPRINT_REJECTION,
        )

        assert result is not None
        assert result.enacted is True

    def test_streak_resets_after_a_proposal_so_the_next_single_failure_does_not_repropose(
        self,
    ) -> None:
        # threshold=2 so a reset streak of 1 stays below it -- with
        # threshold=1 every single failure would legitimately re-trigger
        # (there is no "grace period" concept at that threshold).
        config = StrategyEngineConfig(engine_enabled=True, switch_provider_after_n_challenges=2)
        engine = StrategyEngine(config, now_fn=_fixed_now)
        for _ in range(2):
            engine.decide_switch_provider(
                domain="example.com",
                request_url="https://example.com/",
                current_provider="byparr",
                triggering_failure=FailureCategory.ANTIBOT_FINGERPRINT_REJECTION,
            )
        # 2nd call above already proposed and reset the streak to 0.

        result = engine.decide_switch_provider(
            domain="example.com",
            request_url="https://example.com/",
            current_provider="byparr",
            triggering_failure=FailureCategory.ANTIBOT_FINGERPRINT_REJECTION,
        )
        assert result is None  # streak is now only 1 since the reset

    def test_a_different_provider_breaks_the_streak(self) -> None:
        config = StrategyEngineConfig(engine_enabled=True, switch_provider_after_n_challenges=2)
        engine = StrategyEngine(config, now_fn=_fixed_now)

        engine.decide_switch_provider(
            domain="example.com",
            request_url="https://example.com/",
            current_provider="byparr",
            triggering_failure=FailureCategory.ANTIBOT_FINGERPRINT_REJECTION,
        )
        # A different provider now failing on the same domain (e.g. a
        # human already swapped it) starts a fresh streak, not a
        # continuation of byparr's.
        result = engine.decide_switch_provider(
            domain="example.com",
            request_url="https://example.com/",
            current_provider="camoufox",
            triggering_failure=FailureCategory.ANTIBOT_FINGERPRINT_REJECTION,
        )

        assert result is None  # streak for camoufox is only 1, below threshold 2

    def test_domains_are_tracked_independently(self) -> None:
        config = StrategyEngineConfig(engine_enabled=True, switch_provider_after_n_challenges=1)
        engine = StrategyEngine(config, now_fn=_fixed_now)

        result_a = engine.decide_switch_provider(
            domain="a.example",
            request_url="https://a.example/",
            current_provider="byparr",
            triggering_failure=FailureCategory.ANTIBOT_FINGERPRINT_REJECTION,
        )
        result_b = engine.decide_switch_provider(
            domain="b.example",
            request_url="https://b.example/",
            current_provider="byparr",
            triggering_failure=FailureCategory.ANTIBOT_FINGERPRINT_REJECTION,
        )

        assert result_a is not None
        assert result_b is not None


class TestDecideAdjustBackoff:
    def test_disabled_engine_returns_none(self) -> None:
        engine = StrategyEngine(StrategyEngineConfig(engine_enabled=False), now_fn=_fixed_now)

        result = engine.decide_adjust_backoff(
            domain="example.com",
            request_url="https://example.com/",
            base_cooldown_seconds=60.0,
            target_multiplier=2.0,
            triggering_failure=FailureCategory.NETWORK_INFRA_TRANSIENT,
        )

        assert result is None

    def test_observe_only_computes_but_does_not_enact(self) -> None:
        config = StrategyEngineConfig(
            engine_enabled=True, adjust_backoff_mode=StrategyMode.OBSERVE_ONLY
        )
        engine = StrategyEngine(config, now_fn=_fixed_now)

        result = engine.decide_adjust_backoff(
            domain="example.com",
            request_url="https://example.com/",
            base_cooldown_seconds=60.0,
            target_multiplier=2.0,
            triggering_failure=FailureCategory.NETWORK_INFRA_TRANSIENT,
        )

        assert result is not None
        assert result.enacted is False
        assert result.proposed_action["multiplier"] == 2.0
        assert result.proposed_action["base_cooldown_seconds"] == 60.0
        assert result.proposed_action["adjusted_cooldown_seconds"] == 120.0

    def test_enact_mode_marks_enacted(self) -> None:
        config = StrategyEngineConfig(engine_enabled=True, adjust_backoff_mode=StrategyMode.ENACT)
        engine = StrategyEngine(config, now_fn=_fixed_now)

        result = engine.decide_adjust_backoff(
            domain="example.com",
            request_url="https://example.com/",
            base_cooldown_seconds=60.0,
            target_multiplier=3.0,
            triggering_failure=FailureCategory.NETWORK_INFRA_TRANSIENT,
        )

        assert result is not None
        assert result.enacted is True
        assert result.proposed_action["adjusted_cooldown_seconds"] == 180.0

    def test_target_multiplier_is_clamped_to_the_global_ceiling(self) -> None:
        """Defense in depth: even if a target's own config somehow asked
        for more than the global ceiling allows, the engine itself never
        exceeds it."""
        config = StrategyEngineConfig(
            engine_enabled=True,
            adjust_backoff_mode=StrategyMode.ENACT,
            adjust_backoff_max_multiplier=3.0,
        )
        engine = StrategyEngine(config, now_fn=_fixed_now)

        result = engine.decide_adjust_backoff(
            domain="example.com",
            request_url="https://example.com/",
            base_cooldown_seconds=60.0,
            target_multiplier=4.5,
            triggering_failure=FailureCategory.NETWORK_INFRA_TRANSIENT,
        )

        assert result is not None
        assert result.proposed_action["multiplier"] == 3.0
        assert result.proposed_action["adjusted_cooldown_seconds"] == 180.0


class TestDecideTargetNewUrls:
    """docs/REQUIREMENTS.md section 9 entry 30's own follow-up -- the
    Target Policy Gate."""

    def test_disabled_engine_returns_none_without_inspecting_policy_status(self) -> None:
        engine = StrategyEngine(StrategyEngineConfig(engine_enabled=False), now_fn=_fixed_now)

        result = engine.decide_target_new_urls(
            domain="example.com",
            request_url="https://example.com/",
            candidate_url="https://example.com/new-section",
            policy_status=TargetPolicyStatus.WHITELISTED,
            triggering_failure=FailureCategory.STRUCTURAL_SELECTOR_MISMATCH,
        )

        assert result is None

    def test_locked_raises_value_error(self) -> None:
        engine = StrategyEngine(StrategyEngineConfig(engine_enabled=True), now_fn=_fixed_now)

        with pytest.raises(ValueError, match="locked"):
            engine.decide_target_new_urls(
                domain="example.com",
                request_url="https://example.com/",
                candidate_url="https://example.com/new-section",
                policy_status=TargetPolicyStatus.LOCKED,
                triggering_failure=FailureCategory.STRUCTURAL_SELECTOR_MISMATCH,
            )

    def test_locked_is_the_effective_default_for_any_target(self) -> None:
        """Mirrors SpiderConfig.target_policy_status's own default --
        this is exactly what happens for every existing/unconfigured
        target if a future caller ever reaches this method."""
        engine = StrategyEngine(StrategyEngineConfig(engine_enabled=True), now_fn=_fixed_now)

        with pytest.raises(ValueError):
            engine.decide_target_new_urls(
                domain="example.com",
                request_url="https://example.com/",
                candidate_url="https://example.com/new-section",
                policy_status=TargetPolicyStatus.LOCKED,
                triggering_failure=FailureCategory.UNKNOWN,
            )

    @pytest.mark.parametrize(
        ("status", "expected_proposal_status"),
        [
            (TargetPolicyStatus.WHITELISTED, "accepted-in-principle"),
            (TargetPolicyStatus.PENDING_REVIEW, "pending-human-review"),
            (TargetPolicyStatus.REJECTED, "rejected"),
        ],
    )
    def test_non_locked_statuses_are_recorded_but_never_enacted(
        self, status: TargetPolicyStatus, expected_proposal_status: str
    ) -> None:
        engine = StrategyEngine(StrategyEngineConfig(engine_enabled=True), now_fn=_fixed_now)

        result = engine.decide_target_new_urls(
            domain="example.com",
            request_url="https://example.com/",
            candidate_url="https://example.com/new-section",
            policy_status=status,
            triggering_failure=FailureCategory.STRUCTURAL_SELECTOR_MISMATCH,
        )

        assert result is not None
        assert result.enacted is False  # never -- real enactment is separate, unbuilt work
        assert result.proposed_action["policy_status"] == status.value
        assert result.proposed_action["proposal_status"] == expected_proposal_status
        assert result.proposed_action["candidate_url"] == "https://example.com/new-section"

    def test_pending_review_and_rejected_are_recorded_distinctly(self) -> None:
        """The whole point of the gate: a report must be able to tell
        "still needs a decision" (PENDING_REVIEW) apart from "already
        decided, no" (REJECTED) -- not collapse both into one generic
        "refused" bucket."""
        engine = StrategyEngine(StrategyEngineConfig(engine_enabled=True), now_fn=_fixed_now)

        pending = engine.decide_target_new_urls(
            domain="a.example",
            request_url="https://a.example/",
            candidate_url="https://a.example/new",
            policy_status=TargetPolicyStatus.PENDING_REVIEW,
            triggering_failure=FailureCategory.UNKNOWN,
        )
        rejected = engine.decide_target_new_urls(
            domain="b.example",
            request_url="https://b.example/",
            candidate_url="https://b.example/new",
            policy_status=TargetPolicyStatus.REJECTED,
            triggering_failure=FailureCategory.UNKNOWN,
        )

        assert pending is not None
        assert rejected is not None
        assert (
            pending.proposed_action["proposal_status"]
            != rejected.proposed_action["proposal_status"]
        )
