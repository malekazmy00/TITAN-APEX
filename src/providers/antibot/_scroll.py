"""Shared scroll-to-load helper for
:class:`~src.providers.antibot.camoufox_provider.CamoufoxProvider` and
:class:`~src.providers.antibot.patchright_provider.PatchrightProvider`'s
browser-driving solve functions.

Neither provider had *any* scroll capability before this -- a real,
separately-discovered gap while building the DOM Virtualization round
(docs/REQUIREMENTS.md section 9 entry 13): unlike
``src.middlewares.playwright_middleware.render_with_playwright`` (which
has had this exact scroll-to-stable loop since Phase 2), a target
protected by Anubis (``antibot_needed: true``) is solved entirely by
whichever ``AntibotProvider`` ``ByparrMiddleware`` selects --
``PlaywrightMiddleware``'s own ``process_request`` never runs at all
once an earlier middleware already returned a response, so its scroll
loop was structurally unreachable for any Anubis-protected,
infinite-scroll-shaped target (e.g. test-environment/mock-target's own
``/feed``) the whole time. This module gives Camoufox/Patchright the
identical capability ``render_with_playwright`` already has, reusing its
exact logic.

Typed loosely (``Any`` for the live Playwright/Patchright ``Page``
object) on purpose -- same tradeoff as
``src.providers.antibot._live_dom``'s own module docstring.

**Progressive collection (docs/REQUIREMENTS.md section 9 entry 14):**
:func:`scroll_and_collect` is a second entry point, for the real fix to
entry 13's confirmed DOM Virtualization gap -- reading the page once,
after scrolling finishes, structurally cannot recover content a
virtualized list evicted along the way (it's genuinely gone from the DOM
by then, not merely hidden/encapsulated). Collecting a snapshot after
*every* scroll step instead, before eviction has a chance to remove
what's rendered *right now*, is the fix. :func:`scroll_to_load_lazy_content`
itself is deliberately untouched (no callback, same exact function body
as before) -- zero risk of changing behavior for its own already-proven
callers; this is the version any progressive/incremental strategy builds
on instead.

**Revision (same entry 14, after a real, CI-confirmed failed first
attempt):** :func:`scroll_and_collect` originally copied
:func:`scroll_to_load_lazy_content`'s own "stop once
``document.body.scrollHeight`` stops growing" heuristic -- a real bug
for a *virtualized* target specifically, confirmed via a live CI run
(``html_snapshot_count: 2`` in that run's own structured log line, for
a config that should have kept scrolling): a bounded-window virtualized
list's rendered height never meaningfully grows between scroll steps
(eviction keeps it capped at roughly ``window_size`` posts' worth,
regardless of how much *total* content has loaded) -- so the old
height-growth check always looked like "nothing new happened" after
just one scroll step, even when a later ``loadMore()`` call really did
swap in a genuinely new window of content. This function no longer
reads or compares ``scrollHeight`` at all -- it always performs exactly
``max_attempts`` scroll+collect cycles, relying on ``max_attempts``
itself (already a required, positive, bounded parameter) as the only
stopping condition. It also now dispatches a synthetic ``'scroll'``
``Event`` explicitly, not just ``window.scrollTo(...)`` -- a real,
separate finding from the same investigation: once a virtualized list's
rendered content is short enough to fit within the viewport (which
happens as soon as eviction has trimmed it down at all), calling
``scrollTo()`` with a target position that doesn't actually change
``window.scrollY`` (there's nothing further to scroll *to*) does not
reliably dispatch a real browser ``'scroll'`` event -- and
``templates/feed.html``'s own ``loadMore()`` trigger is bound to that
event, so it would otherwise never fire again after the very first,
automatic (on-page-load) batch. A dispatched event is delivered to the
same listener regardless of whether it's "trusted" (browser-generated)
or synthetic, so this reliably re-triggers the page's own loading logic
on every attempt. :func:`scroll_to_load_lazy_content` is, again,
completely unaffected by any of this -- every other, already
CI-confirmed lazy-load target that relies on it keeps its exact prior
behavior.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def scroll_to_load_lazy_content(page: Any, max_attempts: int, pause_ms: int) -> None:
    """Scroll ``page`` to the bottom repeatedly until its height stops
    growing (or ``max_attempts`` is reached) -- identical logic to
    ``playwright_middleware.py``'s own ``_scroll_to_load_lazy_content``,
    which this shadows deliberately rather than importing (that one stays
    private to its own module; duplicating ~10 lines here is cheaper than
    a cross-middleware/provider import for genuinely equivalent-but-not-
    identical browser objects).

    Harmless (a couple of no-op scroll+wait round trips) on a page that
    doesn't use infinite scroll at all -- the loop exits on its first
    iteration once height stops growing, the same justification
    ``render_with_playwright`` already documents for calling this
    unconditionally.

    Raises:
        ValueError: if ``max_attempts`` is not positive, or ``pause_ms``
            is negative -- both are meaningless configurations.
    """
    if max_attempts <= 0:
        raise ValueError(f"max_attempts must be > 0, got {max_attempts}")
    if pause_ms < 0:
        raise ValueError(f"pause_ms must be >= 0, got {pause_ms}")

    previous_height = page.evaluate("document.body.scrollHeight")
    for _ in range(max_attempts):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(pause_ms)
        current_height = page.evaluate("document.body.scrollHeight")
        if current_height <= previous_height:
            break
        previous_height = current_height


_SCROLL_AND_DISPATCH_SCRIPT = (
    "window.scrollTo(0, document.body.scrollHeight);"
    "window.dispatchEvent(new Event('scroll'));"
)


def scroll_and_collect(
    page: Any, max_attempts: int, pause_ms: int, collect_fn: Callable[[], None]
) -> None:
    """Calls ``collect_fn()`` after *every* read of the page -- including
    the very first, pre-scroll one -- so a caller can capture or extract
    that moment's content before a later step (e.g. DOM Virtualization's
    own eviction) potentially removes it. Always performs exactly
    ``max_attempts`` scroll+collect cycles -- see this module's own
    docstring's "Revision" paragraph for why, unlike
    :func:`scroll_to_load_lazy_content`, this does *not* stop early when
    ``document.body.scrollHeight`` stops growing: that signal is
    meaningless for a bounded-window virtualized target, where rendered
    height never grows much regardless of how much *total* content has
    loaded.

    ``collect_fn`` takes no arguments and returns nothing -- it's expected
    to close over ``page`` itself and accumulate into a caller-owned
    collection (e.g. a dict keyed by post id, for real deduplication
    across steps -- see this module's own docstring for why the id, not
    just "was this call the first time we saw this element", is the
    correct key: an element that's evicted and never comes back needs no
    special handling either way, since it was already captured on an
    earlier step).

    Raises:
        ValueError: if ``max_attempts`` is not positive, or ``pause_ms``
            is negative -- both are meaningless configurations.
    """
    if max_attempts <= 0:
        raise ValueError(f"max_attempts must be > 0, got {max_attempts}")
    if pause_ms < 0:
        raise ValueError(f"pause_ms must be >= 0, got {pause_ms}")

    collect_fn()
    for _ in range(max_attempts):
        page.evaluate(_SCROLL_AND_DISPATCH_SCRIPT)
        page.wait_for_timeout(pause_ms)
        collect_fn()


def collect_html_snapshots(page: Any, max_attempts: int, pause_ms: int) -> list[str]:
    """The ``"parsed_html"`` half of docs/REQUIREMENTS.md section 9 entry
    14's progressive-collection fix: captures ``page.content()`` after
    *every* scroll step via :func:`scroll_and_collect`, instead of just
    the final one, returning every snapshot in order for the caller
    (``generic_spider.py``, via
    :func:`~src.providers.antibot.parsed_html.extract_parsed_html_items`)
    to parse and merge itself -- this module only captures the raw
    strings, since only the caller knows which field is the real identity
    key to deduplicate by.
    """
    snapshots: list[str] = []
    scroll_and_collect(page, max_attempts, pause_ms, lambda: snapshots.append(page.content()))
    return snapshots
