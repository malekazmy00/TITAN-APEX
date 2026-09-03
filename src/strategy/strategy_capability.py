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
    - ``TARGET_NEW_URLS``: reserved for a future round -- proposing a
      URL not already present in a target's own ``start_urls``/
      ``next_page`` chain. Explicit user decision (docs/REQUIREMENTS.md
      section 9 entry 30's own design discussion): no executor exists
      for this anywhere in this codebase yet, and none is built as part
      of this entry -- :class:`StrategyEngineConfig`'s own validator
      refuses any mode for it other than
      :attr:`StrategyMode.DISABLED_NOT_IMPLEMENTED`, so enabling it here
      is a loud, immediate config error rather than a silent no-op.
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
    #: The only legal value for ``TARGET_NEW_URLS`` in this round -- see
    #: that capability's own docstring for why.
    DISABLED_NOT_IMPLEMENTED = "disabled-not-implemented"


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
