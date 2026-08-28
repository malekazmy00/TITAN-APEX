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

**Load-event timeline (same investigation, the "expand the diagnostic
tool" phase explicitly requested after the network-idle-vs-loading-flag
hypothesis was falsified and the separate load_more_calls=0 mystery was
closed -- both by real evidence, not guessing):** neither
``network_idle_timeouts`` nor the ``data-load-more-calls``/
``data-loading-flag`` DOM-attribute samples (camoufox_provider.py's own
``_read_feed_attr``) can see the *exact instant* ``templates/feed.html``'s
``loadMore()`` enters, gets blocked by its own ``loading`` guard, starts
a fetch, the fetch resolves, or the ``loading`` flag actually resets --
they only ever sample state *after* the fact, at whatever moment
``settle_fn`` happens to run. This closes that gap with a real,
in-the-act timeline instead of point samples.

**First attempt, confirmed not to work, not erased:** Playwright's
``page.expose_function()`` (registered on ``page`` before navigation,
its own documented use case for letting page-authored JS call back into
the controlling process) looked like the right crossing direction --
fundamentally different from a plain ``window.*`` expando property, so
it seemed like it should sidestep the Xray-vision isolation this same
entry already confirmed and fixed for ``data-load-more-calls`` (a
*different* crossing direction: this code reading page-set state back,
not the page calling code-provided state). Confirmed by hand with two
separate control cases (a synthetic ``page.set_content()`` page, and a
real ``page.goto()`` navigation) that this assumption was wrong: the
exposed function *is* visible from ``page.evaluate()`` (running in the
same automation-privileged realm ``expose_function`` injects into) but
reads back as ``undefined`` from *inside the page's own inline
``<script>``* -- the mirror image of the original bug, apparently the
same Xray-vision wall cutting both ways on this Camoufox build.

**Corrected approach (this version):** since a DOM attribute set by the
page's own script *does* survive a ``page.evaluate()`` read (the same
confirmed-working channel ``data-load-more-calls``/``data-loading-flag``
already use), the timeline is accumulated in a plain in-page array and
mirrored as one ``JSON.stringify``-ed DOM attribute
(``data-load-event-log``) on every event -- no cross-realm function call
at all. Two deliberate design choices remain, both from an explicit
user instruction to avoid turning the diagnostic into a Heisenbug: (1)
the page's own five call sites (``templates/feed.html``) do nothing but
push into a local array and re-stringify it -- no network/disk I/O, a
handful of tiny objects, microseconds of cost, so no observable delay
is added to ``loadMore()``'s own execution; (2) ``camoufox_provider.py``
reads and parses the attribute *once*, only after progressive collection
is fully done, and writes it to disk (if configured) in that same single
pass -- never a per-event read or write while the race is actually being
timed. A crash mid-collection genuinely has no timeline to report
(the page/browser is gone by the time the crash handler runs) --
documented as a known, accepted gap rather than invented data.

Off by default (``TITAN_DEBUG_LOADING_RACE`` unset, same gate as the
rest of this investigation's diagnostics) -- the file dump itself is
further gated behind ``TITAN_LOAD_EVENT_LOG_DIR`` so a debug run that
only wants the summarized counts in the structured log line doesn't pay
for disk writes it won't read. :func:`load_event_log_dir_from_env`
(reads one env var) and :func:`build_load_event_log_path`/
:func:`render_load_event_log` (pure path/string building) live here for
the same "extract what's testable without a browser" reason as this
module's other functions -- reading/parsing ``data-load-event-log``
itself stays inline in ``camoufox_provider.py``, right next to
``_read_feed_attr``.

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

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any


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


def load_event_log_dir_from_env() -> str | None:
    """Reads ``TITAN_LOAD_EVENT_LOG_DIR`` -- ``None`` (no file dump) when
    unset or empty. Deliberately a *separate* env var from
    ``TITAN_DEBUG_LOADING_RACE`` (the gate that decides whether the
    timeline is collected at all) -- collecting the in-memory timeline
    and writing it to disk are two independent decisions, the same way
    ``TITAN_TRACE_DIR`` is independent of whatever else a run happens to
    be diagnosing.
    """
    value = os.environ.get("TITAN_LOAD_EVENT_LOG_DIR")
    return value if value else None


def build_load_event_log_path(log_dir: str, url: str, provider_name: str) -> str:
    """A unique ``.jsonl`` path for one ``solve()`` call's load-event
    timeline, inside ``log_dir`` -- same uniqueness scheme as
    :func:`build_trace_path` (millisecond timestamp + random suffix;
    the embedded ``url``/``provider_name`` slug is only for a human
    skimming the uploaded artifact, not the uniqueness guarantee
    itself), so many separate crawls can share one directory without
    colliding.

    Raises:
        ValueError: if ``url`` is empty -- meaningless to name a log
            after nothing.
    """
    if not url:
        raise ValueError("url must be non-empty")
    slug = "".join(char if char.isalnum() else "_" for char in url)[:80]
    unique = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    return str(Path(log_dir) / f"{provider_name}_{slug}_{unique}_load_events.jsonl")


def render_load_event_log(events: list[dict[str, Any]]) -> str:
    """Renders the accumulated in-memory load-event timeline as JSONL
    (one JSON object per line) -- pure string building, no filesystem
    access, so this is unit-testable without a browser or a real disk.
    Each provider's own callback appends one plain dict per event as it
    arrives (docs/REQUIREMENTS.md section 9 entry 17's own explicit
    instruction: accumulate in memory, flush to disk *once* after the
    whole solve is over, never per-event I/O while the race is actually
    being timed) -- this is that one flush's own rendering, called
    right before the single ``Path.write_text()`` call. An empty list
    renders as an empty string, not a single blank line.
    """
    return "".join(json.dumps(event, sort_keys=True) + "\n" for event in events)


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
