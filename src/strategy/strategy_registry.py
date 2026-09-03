"""Durable, append-only JSONL log of every :class:`StrategyDecision` --
Layer 3's own audit trail.

Deliberately mirrors
:mod:`src.diagnostics.failure_registry`'s ``record_failure``/
``iter_failures`` almost line for line -- consistency across all 3
layers of this project's diagnosis-and-decision system, not a new
pattern invented per layer. See that module's own docstring for the
full reasoning (a log line alone only survives its CI job's own
retention; a decision a future dashboard needs to render -- enacted or
not -- needs to outlive that).

**Why every call site can safely call :func:`record_decision`
unconditionally, with zero new dependency-injection plumbing:** same
gating as ``failure_registry.py`` -- the real file write only happens
when an explicit ``path`` or the ``TITAN_STRATEGY_DECISION_LOG_PATH``
environment variable is set. ``StrategyEngine`` is directly,
deliberately unit-tested with fakes that trigger every decision path on
purpose; an always-on real default would mean every one of those tests
silently writing fabricated rows into a file meant to hold genuine
decision history.

Never raises: recording a decision must not itself crash the crawl that
triggered it -- the same defensive posture ``failure_registry.py``'s own
``record_failure`` already has.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from src.logging_config import get_logger
from src.strategy.strategy_decision import StrategyDecision

#: Read by :func:`record_decision` when no explicit ``path`` is given --
#: unset (the default in every unit-test process, and in a bare local
#: ``pytest`` run) means "don't write a file at all" -- see this
#: module's own docstring for why.
PATH_ENV_VAR = "TITAN_STRATEGY_DECISION_LOG_PATH"

#: Where the project's own real, historical decision log lives when a
#: caller (or ``.github/workflows/ci.yml``) points
#: ``TITAN_STRATEGY_DECISION_LOG_PATH`` at it -- not itself a hardcoded
#: fallback :func:`record_decision` ever uses on its own.
DEFAULT_PATH = "test-environment/strategy_decisions.jsonl"


def record_decision(
    decision: StrategyDecision, path: str | os.PathLike[str] | None = None
) -> None:
    """Append ``decision`` as one JSON line to the decision-history file.

    Resolves the destination as: explicit ``path`` argument >
    ``TITAN_STRATEGY_DECISION_LOG_PATH`` environment variable > no-op
    (nothing written, no error). Creates parent directories as needed. A
    write failure (disk full, permission denied, ...) is logged as a
    warning and swallowed, never raised.
    """
    logger = get_logger(__name__)
    effective_path = path or os.environ.get(PATH_ENV_VAR)
    if not effective_path:
        return

    try:
        target_path = Path(effective_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with target_path.open("a", encoding="utf-8") as handle:
            handle.write(decision.model_dump_json() + "\n")
    except OSError as exc:
        logger.warning(
            "strategy_registry.write_failed",
            extra={"path": str(effective_path), "reason": str(exc)},
        )


def iter_decisions(path: str | os.PathLike[str]) -> Iterator[StrategyDecision]:
    """Read back every :class:`StrategyDecision` from ``path``, in file
    order. A blank line is skipped; a genuinely malformed line raises
    rather than being silently dropped.

    Raises:
        FileNotFoundError: if ``path`` doesn't exist.
    """
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            yield StrategyDecision.model_validate_json(stripped)
