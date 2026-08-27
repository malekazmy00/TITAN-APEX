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

**AppArmor denial counting (same investigation, a follow-up real user
request):** the monitoring infrastructure's own ``dmesg`` check (
``scripts/ci-check-oom.sh``) found something real but job-wide, not
per-test: 36 AppArmor ``DENIED`` entries for ``camoufox-bin``'s
``userns_create``/``sys_admin`` capability request, recurring through
an entire CI job -- correlated with *a* crash somewhere in that job,
but not attributable to any *one* test from a whole-job count alone.
:func:`count_apparmor_camoufox_denials` is the same "extract the pure,
testable part" split as the rest of this module: it only counts
matching lines in a raw ``dmesg`` text blob handed to it -- reading
``dmesg`` itself (needs ``sudo``, a real Linux kernel ring buffer) is
each provider's own concern, calling this once right before launching
the browser and once right after closing it, logging the delta as
``apparmor_denials_during_solve`` -- the same before/after-delta shape
:class:`~src.providers.antibot._scroll.RequestCounter` already uses,
just for a kernel log instead of network events. Camoufox
(Firefox)-specific on purpose: Patchright's Chromium binary has a
different name and a different sandboxing model, so this pattern
would never match anything for it anyway -- not wired into
``patchright_provider.py`` at all, to avoid a meaningless always-zero
field there.
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


def count_apparmor_camoufox_denials(dmesg_text: str) -> int:
    """Counts AppArmor ``DENIED`` entries for ``camoufox-bin`` in a raw
    ``dmesg`` text blob -- see this module's own docstring for why this
    exists and why it stays Camoufox-specific.

    A real ``dmesg`` line looks like (wrapped here for readability; a
    genuine one is a single line)::

        [  566.812451] audit: type=1400 audit(...): apparmor="DENIED"
        operation="capable" class="cap" profile="unprivileged_userns"
        pid=10915 comm="camoufox-bin" capability=21 capname="sys_admin"

    Matched by plain substring, not a full audit-line parser -- the
    exact field order/spacing across kernel versions isn't a contract
    worth depending on for a diagnostic counter; ``"camoufox-bin"`` and
    ``"DENIED"`` both appearing on the same line is a good enough
    signal, and a false positive here would need another process
    coincidentally named exactly ``camoufox-bin`` also being denied
    something in the same dmesg buffer.
    """
    return sum(1 for line in dmesg_text.splitlines() if "camoufox-bin" in line and "DENIED" in line)


def apparmor_denial_delta(before: int | None, after: int | None) -> int | None:
    """``after - before``, or ``None`` if either snapshot itself is
    ``None`` (dmesg couldn't be read at that point) -- a genuine zero
    delta must never be confused with "couldn't check". Shared by both
    of ``camoufox_provider.py``'s log sites (the normal "solved" path
    and the ``PlaywrightError``/crash path) so the same not-both-None
    guard isn't duplicated at each call site.
    """
    if before is None or after is None:
        return None
    return after - before
