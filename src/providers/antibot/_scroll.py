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

**Fourth revision (docs/REQUIREMENTS.md section 9 entry 17, the actual
root-caused fix -- real, in-the-act evidence from 4 separate failed
solves across a 10-run parallel CI batch, not another guess):** the
``network_idle_timeouts``/`data-load-more-calls`/load-event-timeline
diagnostics (all still active, all unchanged by this revision) proved
the shortfall was never the network settling too slowly -- it was
``templates/feed.html``'s own ``loadMore()`` getting called *twice* for
one scroll step, 1-3ms apart, blocking the second call. Root cause,
confirmed by reading exactly what the old ``_SCROLL_AND_DISPATCH_SCRIPT``
did: ``window.scrollTo(0, document.body.scrollHeight)`` followed by a
*synthetic* ``window.dispatchEvent(new Event('scroll'))`` -- added
originally (this module's own "Revision" paragraph above) because a
plain ``scrollTo()`` alone doesn't reliably fire a real ``'scroll'``
event once a virtualized list's content is short enough to already fit
the viewport. That reasoning was correct on its own, but incomplete: it
assumed the real browser event *never* fires once content is short --
the real-CI timelines proved that assumption wrong. Sometimes a genuine
native ``'scroll'`` event *also* fires (there's still real, if small,
scrollable distance at the exact moment ``scrollTo()`` runs, before
eviction has fully caught up) -- landing on the same
``window.addEventListener("scroll", ...)`` listener as the synthetic
one, so ``loadMore()`` runs twice for what was meant to be one logical
step. The fix removes the synthetic dispatch entirely rather than
trying to suppress the "extra" native one (there is no reliable way to
tell in advance which of the two would have been the "real" one) --
:func:`scroll_and_collect` now drives scrolling with
``page.mouse.wheel(0, delta)``, a genuine Playwright/Patchright
input-level API both engines share identically: it dispatches one real,
trusted wheel/scroll input, the same shape a real mouse produces, with
no separate synthetic JS event to ever race against. ``delta`` and the
pause between scroll steps are both randomized per attempt (via
:func:`randomized_scroll_delta`/:func:`randomized_pause_ms`, both pure
and independently unit-tested with an injected, seedable
``random.Random`` -- no real randomness or browser needed to test
them) rather than the old fixed jump-to-bottom and fixed ``pause_ms``
sleep -- more realistic per-step behavior, and no longer a single
suspiciously-identical timing signature repeated ``max_attempts``
times in a row. :func:`randomized_pause_ms` also folds in a simple,
deliberately early "fatigue"/attention-span model (the mean pause
itself drifts upward as a session progresses, not just i.i.d. jitter
around a constant) -- added now rather than deferred, since harder
future test rounds already anticipated will need the same idea and
retrofitting it later would mean re-deriving this same timing model
twice. ``rng`` defaults to a fresh, unseeded ``random.Random()`` for
every real caller (genuine per-run randomness) -- only this module's
own tests inject a seeded one.

:func:`scroll_to_load_lazy_content` is, again, completely untouched --
it never had the synthetic-dispatch problem in the first place (no
virtualized-list callers use it), and every other already-proven
lazy-load target that relies on it keeps its exact prior behavior.

**Fifth revision (docs/REQUIREMENTS.md section 9 entry 17, real local
evidence gathered right after the "Fourth revision" above, before ever
pushing it): even with the double-dispatch race gone, the shortfall
persisted -- confirmed the real cause is one level deeper.** A source
documenting Playwright's own "trigger-and-wait" pattern for testing
infinite-scroll/lazy-loading pages (a real, cited source, not an
invented justification) names the exact mistake the "Second"/"Third"
revisions' ``settle_fn`` design made: registering a wait *after* the
scroll trigger has already run is itself a race whenever the response
is fast enough to have already arrived. Real, local evidence backed
this up independent of that source: a single ``page.mouse.wheel()``
call was directly observed producing *more than one* genuine, actually
distinct native ``'scroll'`` event (not one -- Firefox headless
appears to animate/replay a large wheel input across more than one
event, confirmed by hand with a bare ``deltaY`` and a live scroll-event
counter), each independently capable of triggering ``loadMore()`` --
so even a perfectly real, single-input wheel scroll could still race a
``settle_fn`` armed only after it, the same way the synthetic dispatch
used to.

``settle_fn`` is removed (not just renamed) in favor of
``trigger_and_wait_fn: Callable[[Callable[[], None]], bool] | None`` --
an injected callable that takes the *actual trigger action itself*
(the ``page.mouse.wheel()`` call this module builds) as an argument,
so the caller can arm whatever real completion listener it needs
*before* invoking it, exactly matching Playwright's own
``page.expect_response()`` context-manager contract (arm, then run the
code that triggers the response, all inside one ``with`` block) --
the same reason this module never built ``settle_fn``'s own concrete
implementation either (:func:`scroll_and_collect`'s own "Second
revision" paragraph above): a provider's concrete wait needs its own
precisely-typed timeout exception, which this module has no business
importing just to catch one. Returns ``True`` once a real response
arrived (scrolling should keep going) or ``False`` on timeout (no new
request was ever sent -- a real, direct signal that pagination has
already ended, not a guess) -- :func:`scroll_and_collect` now stops
its loop early on ``False`` rather than burning through the rest of
``max_attempts`` on guaranteed no-ops. ``None`` (the default) skips
this entirely and just calls the trigger directly, unchanged from
every prior revision's backward-compatibility guarantee.
:func:`poll_until_idle`/:class:`RequestCounter` below stay in this
module as independently-tested, still potentially reusable utilities
for a different future need -- just no longer wired into
:func:`scroll_and_collect` itself, which has no business trusting a
general "is anything happening on the network" signal over the
specific response its own trigger actually caused.

**Sixth revision (docs/REQUIREMENTS.md section 9 entry 17, a real,
confirmed bug in the "Fifth revision" above -- not a race, a plain
ordering mistake, caught by hand against a real load-event timeline
where all 5 pages loaded cleanly and sequentially yet the item count
was still short by exactly one window every time):**
:func:`scroll_and_collect` used to ``break`` on ``trigger_and_wait_fn``
returning ``False`` *before* running that same step's ``pause_ms``
wait and ``collect_fn()`` call. That is wrong whenever the response
that finally reports "no more pages" (e.g. a real provider-side
``page_info.has_next_page: false``) is the *same* response whose own
new content this exact step's trigger just caused to load -- which is
exactly what happens on the last page of a real, finite list: the
final page's own window was successfully appended to the DOM, but the
loop broke before ever calling ``collect_fn()`` again to read it,
silently losing it every single time. The fix: the pause+collect for
the current step always runs regardless of what
``trigger_and_wait_fn`` returned; only the *next* iteration is skipped
when it returns ``False``. This is what this function's own opening
paragraph already promised (``collect_fn()`` runs after *every*
attempted step) -- the "Fifth revision" simply hadn't kept that
promise for its own new early-exit path.

**Seventh revision (docs/REQUIREMENTS.md section 9 entry 17, a real,
user-review-driven follow-up -- not a bug in the "Fourth revision"
above, a real gap in it):** that revision's one-time
``page.mouse.move(200, 200)`` (needed at all only because a bare
``page.mouse.wheel()`` produces zero real scroll with no cursor
positioned somewhere in the viewport first -- see this module's own
comment at that call site) assumed the fixed absolute point
``(200, 200)`` would keep meaning "somewhere sensible over the
scrollable content" for the *entire* crawl. A real, quantified
diagnostic batch (60+ real local runs, docs/REQUIREMENTS.md section 9
entry 17) confirmed this fixed point is *not* what actually causes the
"why does wheel() sometimes need several attempts" pattern (real,
measurable scroll progress -- ~670-682px per attempt -- happens
regardless of what's directly under the fixed point), but it did
confirm a real, separate realism gap: the cursor sits over the same
fixed pixel, almost always the invisible ``virtualization-spacer``
element rather than any real rendered content, for the *entire* crawl
-- not something a real human scrolling a real feed would ever do, and
not guaranteed safe on any real (non-mock) target whose own layout can
shift for reasons this project's own mock target's fixed-shape design
never exercises (a scrollbar appearing/disappearing, a real
re-render moving the scrollable container itself). ``container_selector``
(optional, defaults to ``None``) is the fix: when given, ``page.locator(
container_selector).hover()`` runs immediately before *every* single
scroll attempt -- not once, before the loop -- so the cursor is
re-positioned against the container's own real, current bounding box
every time, the same way a real user's cursor tracks whatever they're
actually looking at rather than staying pinned to one fixed screen
coordinate for an entire session. ``container_selector=None`` (the
default) keeps the exact prior one-time ``page.mouse.move(200, 200)``
behavior unchanged -- every existing caller that hasn't been updated to
supply a selector (and every test written before this revision) keeps
its exact prior behavior, the same backward-compatibility guarantee
this module has kept at every prior revision.

**Eighth revision (docs/REQUIREMENTS.md section 9 entry 17, a real,
CI-confirmed regression the "Seventh revision" above introduced --
caught on real GitHub Actions CI, run 33275376646, not locally: this
module's own test suite never exercised an obscured-container target,
only ``test-environment/mock-target``'s plain ``/feed``):** unlike the
blind ``page.mouse.move()`` it replaced, a real ``Locator.hover()``
performs Playwright's own actionability check -- if another element (a
real, unhandled interstitial overlay, ``position:fixed`` and
full-viewport) currently intercepts pointer events over the container,
``hover()`` correctly waits (its own default: 30 real seconds) rather
than lying about having positioned the cursor, then raises. That
exception was never caught anywhere in this module, so it propagated
all the way up and crashed the *entire* solve -- losing every already-
collected item, not merely this one attempt's contribution -- for a
target this project's own :func:`~src.providers.antibot.
camoufox_provider.CamoufoxProvider`-driven interstitial tests already
expected to degrade *gracefully* (recover whatever loaded before the
block, not crash).

``force=True`` (skipping the actionability check entirely, the same
blind behavior the fixed-coordinate ``move()`` always had) was
considered and rejected: a cited source ("17 Playwright Testing
Mistakes") names it explicitly as an anti-pattern that hides a real
problem instead of fixing it, recommending instead "dismiss the actual
blocking overlay first, don't force past it." ``container_selector``
is replaced (not kept alongside) by ``hover_fn: Callable[[], bool] |
None = None`` -- a caller-supplied callable, called before *every*
scroll attempt exactly where ``container_selector``'s bare ``hover()``
used to run, returning ``True`` if the cursor is genuinely positioned
and it's safe to proceed, ``False`` if positioning failed even after
the caller's own best effort to recover (e.g. dismissing a known
overlay and retrying) -- the same ``bool``-returning,
keep-going-or-stop-early contract :func:`scroll_and_collect` already
established for ``trigger_and_wait_fn``, deliberately reused rather
than inventing a second shape. This mirrors exactly why
``trigger_and_wait_fn`` itself is caller-built rather than implemented
in this module (see this docstring's "Fifth revision"): the retry
itself needs each engine's own precisely-typed timeout exception
(``PlaywrightTimeoutError``/``PatchrightTimeoutError``) to catch, which
this deliberately engine-agnostic module has no business importing.
``hover_fn=None`` (the default) keeps :func:`scroll_and_collect`'s
original, pre-"Seventh revision" one-time ``page.mouse.move(200, 200)``
behavior completely unchanged.
"""

from __future__ import annotations

import random
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


#: Real-mouse-wheel scroll delta range, in pixels (docs/REQUIREMENTS.md
#: section 9 entry 17's "Fourth revision" -- see this module's own
#: module docstring). Deliberately generous at the low end: virtualized
#: content (``DOM_VIRTUALIZATION_WINDOW_SIZE`` posts' worth) is short
#: enough that even the smallest delta in this range reliably clears
#: ``templates/feed.html``'s own "within 200px of the bottom" trigger
#: threshold -- confirmed by hand against the real test-environment
#: stack, not assumed. A fixed jump-to-bottom (the old behavior) would
#: also clear it every time, but always by the exact same, identical
#: amount -- this range keeps that guarantee while no longer producing
#: a single suspiciously-repeated scroll signature.
DEFAULT_SCROLL_DELTA_RANGE_PX = (1500.0, 3000.0)
#: Per-step pause jitter, as a multiplier on the (fatigue-adjusted) mean
#: -- +/-30%.
DEFAULT_PAUSE_JITTER_RANGE = (0.7, 1.3)
#: How much the *mean* pause grows from the first scroll attempt to the
#: last, expressed as a fraction of ``pause_ms`` (0.6 == up to 60% longer
#: by the final attempt) -- the "fatigue"/attention-span model docs/
#: REQUIREMENTS.md section 9 entry 17's "Fourth revision" describes.
DEFAULT_FATIGUE_FACTOR = 0.6


def randomized_scroll_delta(
    rng: random.Random, delta_range: tuple[float, float] = DEFAULT_SCROLL_DELTA_RANGE_PX
) -> float:
    """A random wheel-scroll ``deltaY`` (pixels) for one scroll step --
    replaces the old fixed ``scrollTo(0, document.body.scrollHeight)``
    jump (see this module's own docstring's "Fourth revision"). Pure
    (no browser, no real randomness needed to test -- callers inject a
    seeded ``random.Random`` for deterministic tests).

    Raises:
        ValueError: if ``delta_range``'s low end is negative, or its
            high end is below its low end -- both meaningless.
    """
    low, high = delta_range
    if low < 0:
        raise ValueError(f"delta_range's low end must be >= 0, got {low}")
    if high < low:
        raise ValueError(f"delta_range's high end ({high}) must be >= its low end ({low})")
    return rng.uniform(low, high)


def randomized_pause_ms(
    base_pause_ms: int,
    step_index: int,
    total_steps: int,
    rng: random.Random,
    fatigue_factor: float = DEFAULT_FATIGUE_FACTOR,
    jitter_range: tuple[float, float] = DEFAULT_PAUSE_JITTER_RANGE,
) -> int:
    """A randomized pause (milliseconds) for scroll step ``step_index``
    of ``total_steps`` (both 0-based/plain counts) -- replaces the old
    fixed ``pause_ms`` sleep repeated identically every attempt (see
    this module's own docstring's "Fourth revision"). Two layers, both
    real, not decorative:

    1. A simple cumulative "fatigue"/attention-span model: the *mean*
       pause grows linearly from ``base_pause_ms`` at the first attempt
       to ``base_pause_ms * (1 + fatigue_factor)`` at the last one --
       a session-long drift, not just independent per-step noise.
    2. Independent per-step jitter on top of that mean (uniform within
       ``jitter_range``), so no two attempts -- even at the same
       ``step_index`` across different runs -- land on the same value.

    ``total_steps <= 1`` (no meaningful "progress through the session"
    to model) skips the fatigue drift entirely -- every step uses
    ``base_pause_ms`` as its mean, jitter still applied. Pure (no
    browser, no real time/randomness needed to test).

    Raises:
        ValueError: if ``base_pause_ms`` is negative, ``total_steps`` is
            not positive, or ``step_index`` is outside
            ``[0, total_steps)`` -- all meaningless configurations.
    """
    if base_pause_ms < 0:
        raise ValueError(f"base_pause_ms must be >= 0, got {base_pause_ms}")
    if total_steps <= 0:
        raise ValueError(f"total_steps must be > 0, got {total_steps}")
    if not (0 <= step_index < total_steps):
        raise ValueError(f"step_index ({step_index}) must be in [0, {total_steps})")

    progress = step_index / (total_steps - 1) if total_steps > 1 else 0.0
    mean = base_pause_ms * (1 + fatigue_factor * progress)
    jitter = rng.uniform(*jitter_range)
    return max(0, round(mean * jitter))


def scroll_and_collect(
    page: Any,
    max_attempts: int,
    pause_ms: int,
    collect_fn: Callable[[], None],
    trigger_and_wait_fn: Callable[[Callable[[], None]], bool] | None = None,
    rng: random.Random | None = None,
    hover_fn: Callable[[], bool] | None = None,
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

    ``trigger_and_wait_fn`` (docs/REQUIREMENTS.md section 9 entry 17's
    "Fifth revision" -- see this module's own module docstring for the
    full reasoning, including the cited "trigger-and-wait" source):
    an optional caller-supplied callable, given the actual scroll
    trigger itself (a zero-arg callable this function builds around
    ``page.mouse.wheel()``) to invoke however it needs -- typically by
    arming a real completion listener (e.g.
    ``page.expect_response(...)``) *before* calling it, so the trigger
    and the wait for its own effect can never race. Returns ``True`` if
    scrolling should continue, ``False`` to stop the loop early (a
    real signal -- e.g. no matching response within some timeout --
    that there is nothing left to scroll to, not a guess). ``None``
    (the default) just calls the trigger directly with no wait at
    all -- the exact prior behavior every caller had before this
    parameter existed.

    ``rng`` (docs/REQUIREMENTS.md section 9 entry 17's "Fourth
    revision"): the ``random.Random`` :func:`randomized_scroll_delta`/
    :func:`randomized_pause_ms` draw from. Defaults to a fresh, unseeded
    ``random.Random()`` -- genuine per-call randomness for every real
    caller; only this module's own tests inject a seeded one.

    ``hover_fn`` (docs/REQUIREMENTS.md section 9 entry 17's "Eighth
    revision", replacing the "Seventh revision"'s ``container_selector``
    -- see this module's own module docstring for the full reasoning):
    an optional caller-supplied callable, invoked immediately before
    *every* scroll attempt in place of the fixed ``page.mouse.move(200,
    200)`` every caller used before "Seventh revision" existed. Returns
    ``True`` when the cursor is genuinely positioned over the
    container and it's safe to proceed with this attempt's own scroll
    trigger; ``False`` when positioning failed even after the caller's
    own best-effort recovery (e.g. dismissing a known overlay and
    retrying) -- the same stop-early contract ``trigger_and_wait_fn``
    already has. On ``False``, this attempt's own scroll trigger is
    skipped entirely (there is nothing meaningful to scroll toward),
    but its pause+``collect_fn()`` still runs (same "Sixth revision"
    promise), and the loop stops -- no further attempts. ``None`` (the
    default) keeps the original one-time ``page.mouse.move(200, 200)``
    behavior completely unchanged.

    Raises:
        ValueError: if ``max_attempts`` is not positive, or ``pause_ms``
            is negative -- both are meaningless configurations.
    """
    if max_attempts <= 0:
        raise ValueError(f"max_attempts must be > 0, got {max_attempts}")
    if pause_ms < 0:
        raise ValueError(f"pause_ms must be >= 0, got {pause_ms}")

    rng = rng if rng is not None else random.Random()

    if hover_fn is None:
        # A real, empirically-confirmed requirement, not a stylistic
        # choice: page.mouse.wheel() alone (no prior page.mouse.move())
        # produced zero real scroll at all in a headless Camoufox
        # session (confirmed by hand -- window.scrollY stayed 0 across
        # repeated wheel() calls with no preceding move()) -- the
        # input-level wheel event needs the (virtual) cursor actually
        # positioned somewhere inside the viewport first, the same way
        # a real mouse would already be somewhere on screen before a
        # person starts scrolling. Once, not per attempt -- the cursor
        # stays wherever it's put. Only when no ``hover_fn`` is given at
        # all -- see this function's own "Eighth revision" docstring
        # paragraph for why a real caller should prefer supplying one
        # instead.
        page.mouse.move(200, 200)

    collect_fn()
    for step in range(max_attempts):
        keep_going = True
        if hover_fn is not None:
            # Re-computed fresh on every attempt -- unlike the fixed
            # coordinate above, this stays correct even if the
            # container's own real position shifts between scroll
            # steps (new content appended, old content evicted, a
            # scrollbar appearing/disappearing on a real, non-mock
            # target). See this function's own "Eighth revision"
            # docstring paragraph for why this is a caller-supplied
            # bool-returning callable, not a bare page.locator(...)
            # .hover() call inline here.
            keep_going = hover_fn()

        if keep_going:
            delta = randomized_scroll_delta(rng)

            def _wheel_trigger(delta: float = delta) -> None:
                page.mouse.wheel(0, delta)

            if trigger_and_wait_fn is not None:
                # See this module's own docstring's "Fifth revision" for
                # why the trigger is handed to the caller instead of
                # being run first and waited on afterward -- that
                # ordering is exactly the race this revision closes.
                keep_going = trigger_and_wait_fn(_wheel_trigger)
            else:
                _wheel_trigger()
        # **Sixth revision, a real bug in the "Fifth revision" above,
        # confirmed by hand (docs/REQUIREMENTS.md section 9 entry 17):**
        # the pause+collect for *this* attempt must still run even when
        # `keep_going` is `False` -- the one response that finally says
        # "no more pages" (e.g. a real `has_next_page: false`) is often
        # the exact same response whose own new content this step's
        # trigger just caused to load. Breaking *before* collecting it
        # was a real, confirmed-in-CI-evidence bug: it silently dropped
        # the final page's own window every single time, not a race --
        # this function's own opening paragraph already promises
        # collect_fn() runs after *every* attempted step, no exceptions,
        # and this revision actually keeps that promise. "Eighth
        # revision": the same guarantee now covers a `hover_fn` that
        # returned `False` too, not just `trigger_and_wait_fn`.
        page.wait_for_timeout(randomized_pause_ms(pause_ms, step, max_attempts, rng))
        collect_fn()
        if not keep_going:
            break


def collect_html_snapshots(
    page: Any,
    max_attempts: int,
    pause_ms: int,
    trigger_and_wait_fn: Callable[[Callable[[], None]], bool] | None = None,
    rng: random.Random | None = None,
    hover_fn: Callable[[], bool] | None = None,
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

    ``trigger_and_wait_fn``/``rng``/``hover_fn`` are passed straight
    through to :func:`scroll_and_collect` -- see its own docstring for
    all three.
    """
    snapshots: list[str] = []
    scroll_and_collect(
        page,
        max_attempts,
        pause_ms,
        lambda: snapshots.append(page.content()),
        trigger_and_wait_fn,
        rng,
        hover_fn,
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
