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

**Second revision (docs/REQUIREMENTS.md section 9's "DOM Virtualization
Instability" investigation -- a real, CI-confirmed race the "more
generous constants" fix above only narrowed, never closed):** even
after ``DEFAULT_PROGRESSIVE_SCROLL_PAUSE_MS`` was raised to 1500ms, the
exact same test family kept failing intermittently across later CI runs
-- different item counts every time (21, 20, 0, 24 of an expected 25,
across 7 separate CI attempts), always strictly *below* 25, never above
or exactly on some other fixed number. That shape is the signature of a
**race, not a shortage**: ``templates/feed.html``'s own ``loadMore()``
guards itself with a ``loading`` flag and silently *drops* (not queues,
not retries) any call that arrives while the previous fetch is still in
flight. ``scroll_and_collect`` dispatches the synthetic ``'scroll'``
event and then sleeps a *fixed* ``pause_ms`` before reading/collecting
-- a guess at "how long one fetch+render+trim round trip takes", not a
real synchronization signal. Any time that guess is wrong (real,
variable CI network/CPU load), one of two things happens: the collect
runs *before* that step's fetch has actually landed (a stale read), or
the *next* step's dispatched event fires while the previous fetch is
still in flight and gets silently dropped -- and if that drop happens
to land on the *last* attempt (no further step left to re-trigger it),
that window's items are permanently lost. Both produce exactly the
observed pattern: a random, always-partial count, since it depends on
which attempt(s) the race happens to hit under whatever load that
specific CI run experienced -- not a deterministic, reproducible
shortfall the way the *first* revision's bug was (that one was 100%
reproducible: one scroll step, every time, regardless of load).

The fix is to stop guessing a duration and instead wait for a real
completion signal before collecting. :func:`scroll_and_collect` now
accepts an optional ``settle_fn`` callback, invoked immediately after
the scroll+dispatch step and *before* the existing ``pause_ms`` wait
(which still runs afterward, unchanged, as a final settle buffer for
whatever ``settle_fn`` itself doesn't cover) -- deliberately **not**
implemented inside this module itself: the natural tool for it
(Playwright's ``page.wait_for_load_state("networkidle", ...)``) can
raise a timeout error, and this module stays genuinely engine-agnostic
(the same duck-typed ``Any`` tradeoff its own module docstring already
documents for Camoufox's real Playwright objects vs. Patchright's
structurally-identical-but-distinct ones) -- it has no business
importing either library's own exception type just to catch one. Each
provider supplies its own ``settle_fn`` built around *its own*
precisely-typed timeout exception instead (see
``camoufox_provider.py``/``patchright_provider.py``'s own
``_wait_for_network_idle``). ``settle_fn`` defaults to ``None`` (a
no-op) -- every existing caller that doesn't pass one (including every
unit test written before this revision) keeps its exact prior behavior,
the same backward-compatibility guarantee this module has kept at every
prior revision.

**Third revision (same investigation, a real, CI-confirmed correction
to the "Second revision" above -- not erased, appended):** the
``settle_fn`` contract itself was right, but its first concrete
implementation (in ``camoufox_provider.py``/``patchright_provider.py``,
built around ``page.wait_for_load_state("networkidle", ...)``) was
proven wrong by a real CI run (32973393111): the exact same shortfall
(20 of 25 items) as before ``settle_fn`` existed, unchanged. Root
cause, confirmed against Playwright's own load-state tracking:
``wait_for_load_state`` checks a per-*navigation* lifecycle flag --
once "networkidle" is reached (which happens almost immediately after
the very first, automatic on-load batch here), it stays reached until
the next real navigation, so *every later call* resolves *immediately*
without waiting for anything. Since progressive scrolling never
re-navigates (it is all in-page AJAX against the same document), that
first call is the only one that ever actually waited -- every
subsequent ``settle_fn`` call was a no-op. :func:`poll_until_idle`
below is the corrected building block: a plain, engine-agnostic polling
loop over an injected ``is_idle_fn`` (so it never needs a real browser,
or even real wall-clock time, to unit test) -- each provider supplies
one backed by its *own* live request-tracking (``page.on("request"/
"requestfinished"/"requestfailed")``, maintained continuously across
the whole progressive collection so it can't miss a request that starts
and finishes between two separate polls), a genuinely reusable signal
``wait_for_load_state`` cannot provide for this shape of page.
"""

from __future__ import annotations

import time
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
    page: Any,
    max_attempts: int,
    pause_ms: int,
    collect_fn: Callable[[], None],
    settle_fn: Callable[[], None] | None = None,
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

    ``settle_fn`` (docs/REQUIREMENTS.md section 9's "DOM Virtualization
    Instability" investigation, this module's own docstring's "Second
    revision"): an optional caller-supplied callback invoked right after
    the scroll+dispatch step, before the ``pause_ms`` wait below --
    intended to be a *real* wait-for-completion signal (e.g.
    ``page.wait_for_load_state("networkidle", ...)``) rather than a fixed
    guessed duration. ``None`` (the default) skips it entirely -- the
    exact prior behavior, unchanged.

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
        if settle_fn is not None:
            settle_fn()
        page.wait_for_timeout(pause_ms)
        collect_fn()


def collect_html_snapshots(
    page: Any,
    max_attempts: int,
    pause_ms: int,
    settle_fn: Callable[[], None] | None = None,
) -> list[str]:
    """The ``"parsed_html"`` half of docs/REQUIREMENTS.md section 9 entry
    14's progressive-collection fix: captures ``page.content()`` after
    *every* scroll step via :func:`scroll_and_collect`, instead of just
    the final one, returning every snapshot in order for the caller
    (``generic_spider.py``, via
    :func:`~src.providers.antibot.parsed_html.extract_parsed_html_items`)
    to parse and merge itself -- this module only captures the raw
    strings, since only the caller knows which field is the real identity
    key to deduplicate by.

    ``settle_fn`` is passed straight through to :func:`scroll_and_collect`
    -- see its own docstring.
    """
    snapshots: list[str] = []
    scroll_and_collect(
        page, max_attempts, pause_ms, lambda: snapshots.append(page.content()), settle_fn
    )
    return snapshots


def poll_until_idle(
    is_idle_fn: Callable[[], bool],
    sleep_fn: Callable[[int], None],
    timeout_ms: int,
    quiet_ms: int = 500,
    now_fn: Callable[[], float] = time.monotonic,
) -> bool:
    """The corrected building block for a ``settle_fn`` (this module's own
    "Third revision" docstring paragraph explains what it replaces and
    why): polls ``is_idle_fn()`` -- expected to reflect *live* state, not
    a cached one-time flag -- via ``sleep_fn(50)`` between checks, until
    it has returned ``True`` continuously for ``quiet_ms``, or
    ``timeout_ms`` elapses first. Returns ``True`` if it settled,
    ``False`` on timeout -- a caller that only cares about "did I wait
    long enough" (every current caller) can ignore the return value; it
    exists for callers/tests that want to tell the two apart explicitly.

    Deliberately takes ``is_idle_fn``/``sleep_fn``/``now_fn`` as plain
    injected callables instead of a ``page`` object -- this function
    itself never touches a browser at all (unlike everything else in
    this module), so it needs neither a real one nor real wall-clock
    time to unit test. Each provider supplies ``is_idle_fn`` backed by
    its own live request-tracking and ``sleep_fn``/``now_fn`` backed by
    the real ``page``/``time.monotonic`` respectively.

    Raises:
        ValueError: if ``timeout_ms`` is not positive, or ``quiet_ms`` is
            negative -- both are meaningless configurations, the same
            validation shape :func:`scroll_and_collect` already has.
    """
    if timeout_ms <= 0:
        raise ValueError(f"timeout_ms must be > 0, got {timeout_ms}")
    if quiet_ms < 0:
        raise ValueError(f"quiet_ms must be >= 0, got {quiet_ms}")

    deadline = now_fn() + timeout_ms / 1000
    quiet_since: float | None = None
    poll_interval_ms = 50
    while now_fn() < deadline:
        now = now_fn()
        if is_idle_fn():
            if quiet_since is None:
                quiet_since = now
            elif now - quiet_since >= quiet_ms / 1000:
                return True
        else:
            quiet_since = None
        sleep_fn(poll_interval_ms)
    return False


class RequestCounter:
    """A tiny, purely-local in-flight-request tally -- the ``is_idle_fn``
    each provider hands :func:`poll_until_idle`, backed by *live* request
    state instead of Playwright's own per-navigation ``networkidle``
    cache (see this module's own "Third revision" docstring paragraph
    for why that cache is the wrong signal here).

    :meth:`on_start`/:meth:`on_settle` are meant to be wired directly as
    ``page.on("request", counter.on_start)`` /
    ``page.on("requestfinished", counter.on_settle)`` /
    ``page.on("requestfailed", counter.on_settle)`` listeners -- but
    neither method touches a ``Page`` (or anything else) itself, so this
    class needs no real browser to unit test, unlike the listener wiring
    itself. Both accept and ignore a single positional argument (the
    ``Request`` object Playwright/Patchright passes to the listener) so
    they match that callback shape directly, with no lambda needed at
    the call site.
    """

    def __init__(self) -> None:
        self._pending = 0

    def on_start(self, _event: Any = None) -> None:
        """Call when a request begins (``page.on("request", ...)``)."""
        self._pending += 1

    def on_settle(self, _event: Any = None) -> None:
        """Call when a request ends, successfully or not
        (``page.on("requestfinished"/"requestfailed", ...)``). Clamped
        at zero -- a settle event for a request this counter never saw
        start (e.g. one already in flight before listeners were
        attached) must not push the count negative, which would make
        :meth:`is_idle` wrongly report idle while requests are still
        outstanding.
        """
        self._pending = max(0, self._pending - 1)

    def is_idle(self) -> bool:
        """``True`` when nothing is currently tracked as in flight."""
        return self._pending == 0
