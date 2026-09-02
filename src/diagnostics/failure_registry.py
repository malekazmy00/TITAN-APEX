"""Durable, append-only JSONL history of every classified
:class:`~src.diagnostics.failure_taxonomy.FailureRecord`, from any
source in the project -- Layer 1's actual writer/reader (see
``failure_taxonomy.py``'s own module docstring for the 3-layer plan and
why Layer 1 exists at all).

**Why not just log it (``src/logging_config.py``'s existing JSON-to-
stdout logger)?** every one of this project's real diagnostics
(``scroll_diagnostics``, ``apparmor_denials_during_solve``, provider
``solve_failed`` lines, ``circuit_breaker.opened``, ...) already IS a
structured log line -- and that has a real, confirmed limit this whole
session kept running into by hand: a log line only survives as long as
its CI job's own log retention, and answering "how many failures has
this project ever hit, in category X" means downloading and re-parsing
run after run's raw log zip, every single time (this session did
exactly that, repeatedly, for individual runs -- see docs/REQUIREMENTS.md
entries 24/25/27's own "downloaded and read the full raw log" trail).
This module is that same information, deliberately also written to one
durable, git-tracked file -- ``test-environment/failure_log.jsonl`` by
default -- so a future question like "how many antibot-fingerprint-
rejections have we ever hit against camoufox specifically" is one
``grep``/``jq`` away, not a fresh archaeology project.

**Why every call site can safely call :func:`record_failure` unconditionally,
with zero new dependency-injection plumbing anywhere:** the real file
write only happens when a destination is actually configured (an
explicit ``path=`` argument, or the ``TITAN_FAILURE_LOG_PATH``
environment variable) -- unlike
:mod:`src.providers.antibot.cookie_jar_manager`'s own durable file
(``var/cookie_jar.json``, a *real*, always-on default), which only ever
gets touched from inside a ``# pragma: no cover``, real-browser-only
function no unit test ever reaches, several of this module's own
intended call sites (``CircuitBreakerMiddleware``, ``RateLimiterMiddleware``,
each provider's own ``solve()``/``_solve()``) are directly, deliberately
unit-tested with injected fakes that *trigger* their failure paths on
purpose (a fake provider raising ``AntibotError``, a fake clock forcing
a rate-limit cooldown, ...). An always-on real default there would mean
every one of those pre-existing tests silently writing fabricated rows
into a file meant to hold genuine crawl history, defeating the entire
point. Gating the real write behind an explicit opt-in means calling
:func:`record_failure` from those sites is safe by construction -- no
test needs to know this module exists at all to stay hermetic -- while
:func:`from_env` still resolves the same path everywhere a real
process (this project's own CI, or a real deploy) actually wants it
recorded; ``.github/workflows/ci.yml``'s own "Integration tests" step
sets ``TITAN_FAILURE_LOG_PATH`` for exactly this reason (real crawl
failures against real targets are the only ones worth keeping — a bare
``pytest tests/unit`` run never sets it, so it stays a genuine no-op
there, verified by this module's own tests).

Never raises: recording a failure must not itself crash the crawl that
triggered it -- the same defensive posture every other diagnostic write
in this project already has (``AlertDispatcher``'s own webhook delivery,
``camoufox_provider.py``'s own ``dmesg`` read).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from src.diagnostics.failure_taxonomy import FailureRecord
from src.logging_config import get_logger

#: Read by :func:`record_failure` when no explicit ``path`` is given --
#: unset (the default in every unit-test process, and in a bare local
#: ``pytest`` run) means "don't write a file at all", not "write to
#: some hardcoded default" -- see this module's own docstring for why.
PATH_ENV_VAR = "TITAN_FAILURE_LOG_PATH"

#: Where the project's own real, historical file lives when a caller
#: (or ``.github/workflows/ci.yml``) points ``TITAN_FAILURE_LOG_PATH``
#: at it -- not itself a hardcoded fallback :func:`record_failure` ever
#: uses on its own; exported so callers/CI config have one shared
#: constant to reference instead of retyping the path.
DEFAULT_PATH = "test-environment/failure_log.jsonl"


def record_failure(record: FailureRecord, path: str | os.PathLike[str] | None = None) -> None:
    """Append ``record`` as one JSON line to the failure-history file.

    Resolves the destination as: explicit ``path`` argument >
    ``TITAN_FAILURE_LOG_PATH`` environment variable > no-op (nothing
    written, no error). Creates parent directories as needed. A write
    failure (disk full, permission denied, ...) is logged as a warning
    and swallowed, never raised -- see this module's own docstring for
    why recording a failure must never itself become one.
    """
    logger = get_logger(__name__)
    effective_path = path or os.environ.get(PATH_ENV_VAR)
    if not effective_path:
        return

    try:
        target_path = Path(effective_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with target_path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json() + "\n")
    except OSError as exc:
        logger.warning(
            "failure_registry.write_failed",
            extra={"path": str(effective_path), "reason": str(exc)},
        )


def iter_failures(path: str | os.PathLike[str]) -> Iterator[FailureRecord]:
    """Read back every :class:`FailureRecord` from ``path``, in file
    order -- the read half of this module, for Layer 2/3 (or a report
    like this project's own historical-classification summary) to
    consume without hand-rolling JSONL parsing again. A blank line is
    skipped (harmless if a file was hand-edited); a genuinely malformed
    line raises rather than being silently dropped -- corrupt history
    is worth knowing about, not hiding.

    Raises:
        FileNotFoundError: if ``path`` doesn't exist -- callers that
            want "no history yet" to mean "empty", not an error, should
            check existence first (this function makes no assumption
            about which behavior a given caller wants).
    """
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            yield FailureRecord.model_validate_json(stripped)
