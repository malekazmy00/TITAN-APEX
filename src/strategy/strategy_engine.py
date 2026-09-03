"""Layer 3 ("Strategy Engine") decision logic.

docs/REQUIREMENTS.md section 9, "الطبقة 3": consumes Layer 2's own
real-time classification (:mod:`src.response_classifier`) at the exact
point :class:`~src.middlewares.circuit_breaker.CircuitBreakerMiddleware`
already makes a dispatch decision, and *optionally* refines it -- never
required, never on by default (see
:class:`~src.strategy.strategy_capability.StrategyEngineConfig`'s own
``engine_enabled`` field). ``CircuitBreakerMiddleware`` holds a
:class:`StrategyEngine` instance unconditionally (constructed in its own
``from_crawler``) but every method here returns ``None`` immediately
when the engine is disabled -- so a disabled engine costs one attribute
read per classified rejection, not a code path change anywhere else.

Every real decision (a rotation is proposed, a backoff multiplier is
computed) is recorded via :func:`~src.strategy.strategy_registry.record_decision`
regardless of whether :class:`~src.strategy.strategy_capability.StrategyMode`
is ``OBSERVE_ONLY`` or ``ENACT`` -- the audit trail this module's own
``StrategyDecision`` docstring describes. A "nothing to propose yet"
moment (a streak below threshold, no per-target backoff opt-in) is
*not* recorded at all -- the same "record real signal, not every routine
non-event" discipline
:class:`~src.middlewares.circuit_breaker.CircuitBreakerMiddleware`'s own
``_record_failure`` (below-threshold contributing failures) already
established for Layer 1/2.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from src.diagnostics.failure_taxonomy import FailureCategory
from src.strategy.strategy_capability import (
    StrategyCapability,
    StrategyEngineConfig,
    StrategyMode,
    TargetPolicyStatus,
)
from src.strategy.strategy_decision import StrategyDecision
from src.strategy.strategy_registry import record_decision

#: TARGET_NEW_URLS's own Target Policy Gate (docs/REQUIREMENTS.md
#: section 9 entry 30's own follow-up) -- what a proposal registers as
#: for each non-LOCKED :class:`~src.strategy.strategy_capability.TargetPolicyStatus`.
#: LOCKED itself (and any future, unrecognized status -- ``.get()``
#: below returns ``None`` for both) is handled separately, by raising,
#: not by a dict entry: there is no "proposal status" to record for a
#: refusal, only the refusal itself.
_PROPOSAL_STATUS_BY_POLICY: dict[TargetPolicyStatus, str] = {
    TargetPolicyStatus.WHITELISTED: "accepted-in-principle",
    TargetPolicyStatus.PENDING_REVIEW: "pending-human-review",
    TargetPolicyStatus.REJECTED: "rejected",
}

#: SWITCH_PROVIDER's own rotation order -- a deliberately simple, fixed
#: cycle (not a ranking learned from history) matching this round's own
#: honesty-over-sophistication choice (see StrategyEngineConfig's own
#: ``switch_provider_after_n_challenges`` docstring).
PROVIDER_ROTATION: tuple[str, ...] = ("byparr", "camoufox", "patchright")


def _next_in_rotation(current: str) -> str:
    try:
        index = PROVIDER_ROTATION.index(current)
    except ValueError:
        # An unrecognized provider name (shouldn't happen -- SpiderConfig's
        # own Literal["byparr", "camoufox", "patchright"] already
        # constrains this at config-load time) falls back to the start
        # of the rotation rather than raising -- a decision engine must
        # never itself crash the crawl it's trying to help.
        return PROVIDER_ROTATION[0]
    return PROVIDER_ROTATION[(index + 1) % len(PROVIDER_ROTATION)]


class StrategyEngine:
    """Stateful (per-domain streak tracking, in-memory, one instance per
    crawler lifetime -- same shape as
    ``CircuitBreakerMiddleware._circuits``) decision-maker for the
    capabilities this layer implements: ``decide_switch_provider`` and
    ``decide_adjust_backoff`` (both wired into a real, live-network-
    confirmed call site, ``CircuitBreakerMiddleware``), and
    ``decide_target_new_urls`` (pure internal decision logic -- its own
    Target Policy Gate, docs/REQUIREMENTS.md section 9 entry 30's own
    follow-up -- with no real caller anywhere in this codebase yet;
    that capability's actual enactment is still future, unbuilt work).
    """

    def __init__(
        self,
        config: StrategyEngineConfig,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self._now = now_fn or (lambda: datetime.now(tz=UTC))
        # domain -> (provider currently on a challenge streak, count)
        self._challenge_streaks: dict[str, tuple[str, int]] = {}

    def decide_switch_provider(
        self,
        *,
        domain: str,
        request_url: str,
        current_provider: str,
        triggering_failure: FailureCategory,
    ) -> StrategyDecision | None:
        """Call once per real ``CHALLENGE_PAGE`` classification against
        ``domain``. Returns ``None`` (no decision, nothing recorded)
        whenever the engine is disabled, or the streak hasn't yet
        reached ``switch_provider_after_n_challenges`` -- only a
        genuine, actionable proposal is ever recorded.

        After a proposal fires (whether enacted or not), the streak
        resets to 0 -- observe-only mode still gets one proposal per
        threshold crossing, not a duplicate on every single subsequent
        failure while the underlying problem persists (mirrors
        ``CircuitBreakerMiddleware``'s own "record once, not every
        contributing event" discipline).
        """
        if not self.config.engine_enabled:
            return None

        provider = current_provider or PROVIDER_ROTATION[0]
        streak_provider, streak_count = self._challenge_streaks.get(domain, (provider, 0))
        streak_count = streak_count + 1 if streak_provider == provider else 1
        self._challenge_streaks[domain] = (provider, streak_count)

        if streak_count < self.config.switch_provider_after_n_challenges:
            return None

        next_provider = _next_in_rotation(provider)
        mode = self.config.switch_provider_mode
        enacted = mode is StrategyMode.ENACT
        decision = StrategyDecision(
            timestamp=self._now(),
            target=request_url,
            capability=StrategyCapability.SWITCH_PROVIDER,
            triggering_failure=triggering_failure,
            proposed_action={
                "current_provider": provider,
                "new_provider": next_provider,
                "streak": streak_count,
            },
            mode_at_decision_time=mode,
            enacted=enacted,
            source="strategy_engine.switch_provider",
        )
        record_decision(decision)
        self._challenge_streaks[domain] = (next_provider, 0) if enacted else (provider, 0)
        return decision

    def decide_adjust_backoff(
        self,
        *,
        domain: str,
        request_url: str,
        base_cooldown_seconds: float,
        target_multiplier: float,
        triggering_failure: FailureCategory,
    ) -> StrategyDecision | None:
        """Call once per circuit-open event on a target that opted in
        (``SpiderConfig.strategy_backoff_multiplier`` set -- the caller,
        ``CircuitBreakerMiddleware``, is responsible for not calling
        this at all for a target that hasn't; see that middleware's own
        ``_resolve_cooldown``). Returns ``None`` only when the engine
        itself is disabled -- an opted-in target always gets a real,
        recorded decision, even in ``OBSERVE_ONLY`` mode, since there is
        always something concrete to propose here (unlike
        ``decide_switch_provider``'s streak-gated shape).

        ``target_multiplier`` is clamped to
        ``StrategyEngineConfig.adjust_backoff_max_multiplier`` -- the
        absolute ceiling -- regardless of what the target's own config
        requested (defense in depth on top of that field's own
        ``Field(..., le=...)`` bound, enforced again here).
        """
        if not self.config.engine_enabled:
            return None

        multiplier = min(target_multiplier, self.config.adjust_backoff_max_multiplier)
        adjusted_cooldown_seconds = base_cooldown_seconds * multiplier
        mode = self.config.adjust_backoff_mode
        decision = StrategyDecision(
            timestamp=self._now(),
            target=request_url,
            capability=StrategyCapability.ADJUST_BACKOFF,
            triggering_failure=triggering_failure,
            proposed_action={
                "multiplier": multiplier,
                "base_cooldown_seconds": base_cooldown_seconds,
                "adjusted_cooldown_seconds": adjusted_cooldown_seconds,
            },
            mode_at_decision_time=mode,
            enacted=mode is StrategyMode.ENACT,
            source="strategy_engine.adjust_backoff",
        )
        record_decision(decision)
        return decision

    def decide_target_new_urls(
        self,
        *,
        domain: str,
        request_url: str,
        candidate_url: str,
        policy_status: TargetPolicyStatus,
        triggering_failure: FailureCategory,
    ) -> StrategyDecision | None:
        """docs/REQUIREMENTS.md section 9 entry 30's own follow-up (the
        Target Policy Gate) -- a genuinely separate axis from
        ``StrategyEngineConfig.target_new_urls_mode`` (which stays
        hard-locked to ``DISABLED_NOT_IMPLEMENTED`` regardless of what
        this method does; no real enactment of ``TARGET_NEW_URLS``
        exists anywhere in this codebase, and none is built here
        either). This method governs a narrower, purely internal
        question: when *something* -- no real caller exists in this
        codebase yet, this method is preparatory, directly testable
        infrastructure for one that will exist once real
        new-target-discovery logic is built -- considers proposing
        ``candidate_url`` as a new crawl target, is even *registering
        that proposal* allowed for ``domain``, or is it refused
        outright?

        Returns ``None`` immediately if the engine itself is disabled --
        same as every other ``decide_*`` method here; ``policy_status``
        is never even inspected in that case.

        Every non-``LOCKED`` outcome is recorded via
        :func:`~src.strategy.strategy_registry.record_decision` with
        ``enacted`` always ``False`` -- registering a proposal as
        accepted-in-principle, pending, or rejected is never itself an
        enactment (real enactment, when it eventually exists, is a
        wholly separate, later decision this method has no opinion on).

        Raises:
            ValueError: when ``policy_status`` is
                :attr:`~src.strategy.strategy_capability.TargetPolicyStatus.LOCKED`
                (every target's own default -- see
                ``SpiderConfig.target_policy_status``), or any future,
                unrecognized status -- fail-closed either way, the exact
                same hard stop ``TARGET_NEW_URLS`` has always had for
                every target until a human explicitly whitelists,
                queues for review, or rejects it in that target's own
                config file. A real future caller (none exists yet)
                will need its own decision about how to handle this
                without crashing an active crawl -- explicitly out of
                this entry's own scope.
        """
        if not self.config.engine_enabled:
            return None

        proposal_status = _PROPOSAL_STATUS_BY_POLICY.get(policy_status)
        if proposal_status is None:
            raise ValueError(
                f"target_policy_status is {policy_status.value!r} for domain "
                f"{domain!r} -- StrategyCapability.TARGET_NEW_URLS proposals "
                f"are refused for this target until a human explicitly "
                f"changes its target_policy_status in that target's own "
                f"config file (docs/REQUIREMENTS.md section 9 entry 30's own "
                f"explicit design decision: never an automatic transition)."
            )

        decision = StrategyDecision(
            timestamp=self._now(),
            target=request_url,
            capability=StrategyCapability.TARGET_NEW_URLS,
            triggering_failure=triggering_failure,
            proposed_action={
                "candidate_url": candidate_url,
                "policy_status": policy_status.value,
                "proposal_status": proposal_status,
            },
            mode_at_decision_time=self.config.target_new_urls_mode,
            enacted=False,
            source="strategy_engine.target_new_urls",
        )
        record_decision(decision)
        return decision
