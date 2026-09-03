"""Layer 3 ("Strategy Engine") control-point schema.

docs/REQUIREMENTS.md section 9, "الطبقة 3": the user's own explicit
architectural requirement, given before any of this layer's decision
logic was designed, verbatim -- every action this layer can take must
sit behind an individually toggleable flag/config, never hardcoded as
permanent behavior, so that a future dashboard (deliberately deferred --
see that entry's own notes) can operate directly on these exact control
points without re-architecting this layer at all. Same "interface-first"
principle Phase 1's ``AntibotProvider``/``StorageBackend`` interfaces
already established for provider/storage swapping -- here applied to
*decision authority* instead.

This module is schema only (mirrors
``src.diagnostics.failure_taxonomy``'s own scope discipline for Layer 1:
just the shape, zero I/O, zero decision logic -- see
``src.strategy.strategy_engine`` for the logic that actually reads this
config).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class StrategyCapability(StrEnum):
    """One independently toggleable action the Strategy Engine can take.

    - ``SWITCH_PROVIDER``: propose (or, if enacted, apply) a different
      ``antibot_provider`` for a target's *next retry attempt only* --
      never a permanent change to the target's own YAML config on disk.
    - ``ADJUST_BACKOFF``: propose (or, if enacted, apply) a bounded
      multiplier on top of the cooldown a classified failure would
      otherwise get, on this domain's circuit *only* -- never below the
      target's own configured minimum (never undermines
      ``RateLimiterMiddleware``'s own budget).
    - ``TARGET_NEW_URLS``: proposing a URL not already present in a
      target's own ``start_urls``/``next_page`` chain. This
      capability's own *enactment* mode
      (:attr:`StrategyEngineConfig.target_new_urls_mode`) stays
      hard-locked to :attr:`StrategyMode.DISABLED_NOT_IMPLEMENTED` --
      no executor that actually crawls a newly-proposed URL exists
      anywhere in this codebase, and none is built here either.
      **Correction to an earlier design note** (docs/REQUIREMENTS.md
      section 9 entry 30's own follow-up, the same entry): this
      capability was first described as "reserved for a future
      round, deferred to the dashboard" -- on review that was wrong.
      *Deciding whether a proposal is even allowed to be registered*
      (accepted-in-principle, pending human review, previously
      rejected, or refused outright) is pure internal decision logic,
      the exact same shape every other capability here already has --
      it needs no user interface at all, so it belongs in this layer
      now, not deferred alongside the dashboard. See
      :class:`TargetPolicyStatus` and
      :meth:`~src.strategy.strategy_engine.StrategyEngine.decide_target_new_urls`
      for that gate -- a genuinely separate axis from this capability's
      own enactment mode above, which stays locked regardless.
    """

    SWITCH_PROVIDER = "switch-provider"
    ADJUST_BACKOFF = "adjust-backoff"
    TARGET_NEW_URLS = "target-new-urls"


class StrategyMode(StrEnum):
    """How a given :class:`StrategyCapability` is currently allowed to act."""

    #: Compute and record (via :mod:`src.strategy.strategy_registry`)
    #: what the engine WOULD do -- never mutates anything a real
    #: request/response actually sees. The default for every capability,
    #: without exception, and the only mode possible at all while
    #: :attr:`StrategyEngineConfig.engine_enabled` is False.
    OBSERVE_ONLY = "observe-only"
    #: Actually apply the decision -- bounded per-capability; see each
    #: capability's own executor in ``strategy_engine.py`` for its exact
    #: limits (never a permanent config-file mutation, for either
    #: capability that supports this mode today).
    ENACT = "enact"
    #: The only legal value for ``TARGET_NEW_URLS``'s own *enactment*
    #: mode -- see that capability's own docstring for why (unrelated to
    #: :class:`TargetPolicyStatus` below, which governs a narrower,
    #: separate question).
    DISABLED_NOT_IMPLEMENTED = "disabled-not-implemented"


class TargetPolicyStatus(StrEnum):
    """Per-target gate for :attr:`StrategyCapability.TARGET_NEW_URLS`
    proposals -- docs/REQUIREMENTS.md section 9 entry 30's own
    follow-up, a correction of that capability's first design note (see
    its own docstring). Set on a target's own
    :class:`~src.spiders.spider_config.SpiderConfig` via
    ``target_policy_status`` -- **never** changed by the engine itself;
    only a human, editing that target's own config file, moves a target
    out of the default.

    This governs one narrow question only: when the engine considers
    proposing a URL not already in a target's own crawl scope, is even
    *registering that proposal* (as a :class:`~src.strategy.strategy_decision.StrategyDecision`)
    allowed for this specific target? It says nothing about whether the
    proposal is ever actually acted on -- real enactment of
    ``TARGET_NEW_URLS`` stays a separate, unbuilt question (that
    capability's own ``target_new_urls_mode``, hard-locked to
    :attr:`StrategyMode.DISABLED_NOT_IMPLEMENTED` regardless of any
    target's policy status here).

    - ``WHITELISTED``: a human has explicitly cleared this target for
      new-URL proposals -- registered as accepted-in-principle.
    - ``PENDING_REVIEW``: a proposal for this target needs a human
      decision -- registered as exactly that, not silently treated as
      approval or refusal either way.
    - ``REJECTED``: a human already reviewed and decided against this
      target -- registered as a real, final decision, distinct from
      ``PENDING_REVIEW``'s open question (so a future report can tell
      "still needs a decision" apart from "already decided, no").
    - ``LOCKED``: the default for every target that hasn't set this
      field at all, and the fail-closed state generally -- refuses the
      proposal outright (``ValueError``), the exact same hard stop
      ``TARGET_NEW_URLS`` has always had for every target. Changing a
      target away from ``LOCKED`` is a deliberate, manual edit to that
      target's own config file -- never automatic, never the engine's
      own decision (a legal/human judgment call, not a heuristic one).
    """

    WHITELISTED = "whitelisted"
    PENDING_REVIEW = "pending-review"
    REJECTED = "rejected"
    LOCKED = "locked"


class StrategyEngineConfig(BaseModel):
    """docs/REQUIREMENTS.md section 9 entry 30's own control-point
    surface. Every field here is meant to be the *exact* thing a future
    dashboard reads and writes -- whether that's via environment
    variables (today, read the same way
    ``CircuitBreakerMiddleware.from_crawler`` already reads its own
    settings) or a persisted config store the dashboard maintains
    (later) -- nothing about this shape is expected to change when that
    dashboard is actually built.
    """

    # The master switch -- explicit user decision: off by default, and
    # while it's off, CircuitBreakerMiddleware never even constructs or
    # consults a StrategyEngine at all (zero behavior change anywhere
    # else in the codebase -- see circuit_breaker.py's own wiring).
    engine_enabled: bool = False
    switch_provider_mode: StrategyMode = StrategyMode.OBSERVE_ONLY
    adjust_backoff_mode: StrategyMode = StrategyMode.OBSERVE_ONLY
    # Absolute ceiling no target's own per-target multiplier
    # (SpiderConfig.strategy_backoff_max_multiplier) may ever exceed --
    # defense in depth even if a target's own YAML is misconfigured
    # (that field's own Field(..., le=...) bound already enforces the
    # same ceiling independently, at config-load time, before this
    # engine is ever consulted).
    adjust_backoff_max_multiplier: float = Field(default=5.0, gt=1.0, le=5.0)
    # After this many consecutive CHALLENGE_PAGE classifications against
    # the same domain while using the same provider, SWITCH_PROVIDER
    # proposes rotating to the next provider (see strategy_engine.py's
    # own PROVIDER_ROTATION) -- a deliberately simple, bounded, and
    # honest heuristic (a fixed rotation, not an ML ranking) rather than
    # overpromising intelligence this round never built.
    switch_provider_after_n_challenges: int = Field(default=2, ge=1)
    # Only legal value in this round -- see StrategyCapability's own
    # TARGET_NEW_URLS docstring.
    target_new_urls_mode: StrategyMode = StrategyMode.DISABLED_NOT_IMPLEMENTED

    @model_validator(mode="after")
    def _target_new_urls_has_no_executor_yet(self) -> StrategyEngineConfig:
        if self.target_new_urls_mode is not StrategyMode.DISABLED_NOT_IMPLEMENTED:
            raise ValueError(
                "target_new_urls_mode must stay DISABLED_NOT_IMPLEMENTED -- "
                "no executor for StrategyCapability.TARGET_NEW_URLS exists "
                "in this codebase yet (docs/REQUIREMENTS.md section 9 entry "
                "30's own explicit design decision, confirmed with the "
                "user before this module was written); enabling it here "
                "would silently do nothing real, which this project's own "
                "'never silent' discipline never allows -- raising loudly "
                "here instead."
            )
        return self
