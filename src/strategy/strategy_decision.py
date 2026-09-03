"""One record of a Strategy Engine decision -- Layer 3's own schema.

docs/REQUIREMENTS.md section 9, "الطبقة 3": mirrors
``src.diagnostics.failure_taxonomy.FailureRecord``'s own design
deliberately -- every decision the engine makes gets recorded, enacted
or not, the same "audit trail from day one" principle Layer 1 already
established, so a future dashboard has full decision history to render
even for a period when the engine only ever ran in
``StrategyMode.OBSERVE_ONLY``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from src.diagnostics.failure_taxonomy import FailureCategory
from src.strategy.strategy_capability import StrategyCapability, StrategyMode


class StrategyDecision(BaseModel):
    """One decision the Strategy Engine made -- whether it was actually
    applied or just recorded for later review.

    ``target``: the domain or URL the decision concerns -- whichever is
    most specific and available at the call site (mirrors
    ``FailureRecord.target``'s own reasoning).

    ``triggering_failure``: which :class:`~src.diagnostics.failure_taxonomy.FailureCategory`
    prompted this decision -- always ``ANTIBOT_FINGERPRINT_REJECTION`` in
    this round (the only category ``CircuitBreakerMiddleware``'s
    classified-rejection path, this engine's sole real-time hook, ever
    produces), kept as a real field rather than hardcoded so a later
    entry wiring more failure categories into Layer 3 doesn't need a
    schema change here.

    ``proposed_action``: whatever the engine decided to do, or would
    have done -- e.g. ``{"new_provider": "camoufox", "streak": 2}`` for
    :attr:`~src.strategy.strategy_capability.StrategyCapability.SWITCH_PROVIDER`,
    or ``{"multiplier": 2.0, "base_cooldown_seconds": 60.0,
    "adjusted_cooldown_seconds": 120.0}`` for
    :attr:`~src.strategy.strategy_capability.StrategyCapability.ADJUST_BACKOFF`.
    Deliberately untyped (``dict[str, Any]``), the same reasoning
    ``FailureRecord.raw_signal`` already has: each capability's shape is
    genuinely different.

    ``enacted``: True only when :attr:`mode_at_decision_time` was
    :attr:`~src.strategy.strategy_capability.StrategyMode.ENACT` *and*
    the engine actually applied it to a real request/circuit -- never
    True for a purely computed, observational decision.

    ``source``: which module/event produced this record (e.g.
    ``"strategy_engine.switch_provider"``) -- the same provenance
    ``FailureRecord.source`` already requires, for the same reason.
    """

    timestamp: datetime
    target: str
    capability: StrategyCapability
    triggering_failure: FailureCategory
    proposed_action: dict[str, Any] = Field(default_factory=dict)
    mode_at_decision_time: StrategyMode
    enacted: bool
    source: str
