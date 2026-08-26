"""Optional Playwright/Patchright trace capture for
:class:`~src.providers.antibot.camoufox_provider.CamoufoxProvider` and
:class:`~src.providers.antibot.patchright_provider.PatchrightProvider`'s
browser-driving solve functions.

docs/REQUIREMENTS.md section 9 entry 17's monitoring-infrastructure
investment: a real, user-requested one-time addition after two CI
attempts of a code-only fix for the DOM Virtualization race left the
actual root cause unconfirmed (the new ``network_idle_timeouts``
diagnostic proved the network side was never the bottleneck -- CI run
32997246624 -- yet the same shortfall happened anyway). Rather than
guess a third fix blind, this gives any future timing/resource
investigation (not just entry 17) a real trace to open instead of
re-deriving evidence by hand every time: a Playwright trace captures a
full timeline (screenshots, DOM snapshots, network, console, and --
since ``sources=True`` -- source-mapped stack traces) for the entire
``solve()`` call, viewable at https://trace.playwright.dev/ or via
``playwright show-trace``.

Off by default (zero overhead, zero behavior change for every existing
caller) -- active only when the ``TITAN_TRACE_DIR`` environment
variable is set, e.g. by ``.github/workflows/ci.yml`` during the
"Integration tests" step. Deliberately not itself a browser-driving
call: only :func:`trace_dir_from_env` (reads one env var) and
:func:`build_trace_path` (pure string/path building) live here, so
both are fully unit-testable without a real browser -- the
``page.context.tracing.start()``/``.stop()`` calls themselves stay
inline in each provider's own solve function, right next to the
``Page``/``BrowserContext`` objects they need.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path


def trace_dir_from_env() -> str | None:
    """Reads ``TITAN_TRACE_DIR`` -- ``None`` (tracing off) when unset or
    empty, matching every other optional-capability env var this
    project reads on demand rather than at import time (src/settings.py's
    own established pattern)."""
    value = os.environ.get("TITAN_TRACE_DIR")
    return value if value else None


def build_trace_path(trace_dir: str, url: str, provider_name: str) -> str:
    """A unique ``.zip`` path for one ``solve()`` call's trace, inside
    ``trace_dir``.

    Many separate crawls (different targets, or the same target solved
    more than once) can share one ``TITAN_TRACE_DIR`` within a single CI
    job -- the whole directory is uploaded as one artifact -- so this
    must never collide, even for the exact same ``url`` solved twice in
    a row. The filename embeds ``provider_name`` and a sanitized,
    truncated ``url`` purely so a human skimming the uploaded artifact
    can tell traces apart without opening each one; the actual
    uniqueness guarantee comes from the millisecond timestamp + a random
    suffix, not from the url/provider text.

    Raises:
        ValueError: if ``url`` is empty -- meaningless to name a trace
            after nothing.
    """
    if not url:
        raise ValueError("url must be non-empty")
    slug = "".join(char if char.isalnum() else "_" for char in url)[:80]
    unique = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    return str(Path(trace_dir) / f"{provider_name}_{slug}_{unique}.zip")
