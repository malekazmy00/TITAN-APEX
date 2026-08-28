"""Camoufox implementation of the :class:`AntibotProvider` interface.

Camoufox (https://github.com/daijro/camoufox, MPL-2.0) is a real,
Firefox-based stealth browser with a Playwright-compatible Python API
(``camoufox.sync_api.Camoufox`` subclasses Playwright's own
``PlaywrightContextManager`` -- ``browser.new_page()``/``page.goto()``
return genuine Playwright objects).

**This is not "the same engine Byparr uses, minus its API layer" --
that was checked directly against Byparr's own real ``pyproject.toml``
before writing this, and it is a different, unrelated stealth-Firefox
project.** Byparr depends on plain ``playwright`` plus
``invisible-playwright`` (a separate patched-Firefox stealth project
that explicitly lists Camoufox as a *comparison*, not a shared
dependency, in its own docs) and ``playwright-captcha`` -- no
``camoufox`` package anywhere in it. So swapping to this provider is a
genuine engine change, not just bypassing a layer Byparr happens to
wrap around the same browser.

What *is* true, and is the actual reason this provider exists: unlike
:class:`~src.providers.antibot.byparr_provider.ByparrProvider` (which
delegates to Byparr's own external HTTP API and has no control over when
Byparr's browser closes), this provider drives its browser itself,
in-process -- giving it direct control over the browser's lifecycle.
docs/REQUIREMENTS.md section 9 entry 4 found, by direct observation,
that Byparr's ``/v1`` API tears its browser down as soon as the page's
``load`` event fires, before a challenge that does real work
*asynchronously after* ``load`` (like Anubis's real proof-of-work flow)
ever gets a chance to finish -- a constraint of Byparr's own API
contract, not of any particular browser engine. This provider adds an
explicit, configurable wait *after* ``load`` before reading the page and
closing the browser -- the same ``render_wait_ms`` idea already proven
for :func:`~src.middlewares.playwright_middleware.render_with_playwright`,
applied here to the antibot-solving path instead of the render path. See
:mod:`src.providers.antibot.patchright_provider` for a second,
lighter-weight provider built on the *same* idea (hold the browser open
past ``load``) using a Chromium engine instead of Camoufox's Firefox.

The actual browser-driving call is injectable (``solve_fn``) so unit
tests never launch a real browser or touch the network.

**Scroll capability (docs/REQUIREMENTS.md section 9 entry 13):** this
provider had none at all before -- unlike
:func:`~src.middlewares.playwright_middleware.render_with_playwright`,
which has scrolled to load lazy content since Phase 2, that middleware's
own ``process_request`` never actually runs for an Anubis-protected
target (``antibot_needed: true``), since ``ByparrMiddleware`` already
returns a response first and Scrapy stops walking the downloader-
middleware chain once one does. Any Anubis-protected, infinite-scroll-
shaped target (test-environment/mock-target's own ``/feed``) was
structurally unreachable by that scroll loop, discovered while building
the DOM Virtualization round -- see
:func:`~src.providers.antibot._scroll.scroll_to_load_lazy_content`,
called unconditionally here now, the identical logic and justification
``render_with_playwright`` already established.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from logging import Logger
from pathlib import Path
from typing import Any, NamedTuple

from src.core.exceptions import AntibotError
from src.core.interfaces.antibot_provider import (
    AntibotProvider,
    LiveDomSelectors,
    LoginFlow,
    Solution,
)
from src.logging_config import get_logger
from src.providers.antibot._live_dom import (
    collect_live_dom_items_progressively,
    extract_live_dom_items,
)
from src.providers.antibot._login import log_login_outcome, perform_login_and_navigate
from src.providers.antibot._scroll import (
    RequestCounter,
    collect_html_snapshots,
    poll_until_idle,
    scroll_to_load_lazy_content,
)
from src.providers.antibot._tracing import (
    apparmor_denial_delta,
    build_load_event_log_path,
    build_trace_path,
    count_apparmor_camoufox_denials,
    load_event_log_dir_from_env,
    render_load_event_log,
    trace_dir_from_env,
)

DEFAULT_TIMEOUT_MS = 30_000
# How long to wait after the page's `load` event before reading content
# and closing the browser -- long enough for a fast (difficulty-2, per
# Anubis's own shipped thresholds) proof-of-work challenge to compute and
# round-trip, confirmed sufficient by hand against the real
# test-environment/ stack (docs/REQUIREMENTS.md section 9 entry 4/round 3).
DEFAULT_POST_LOAD_WAIT_MS = 5_000
# docs/REQUIREMENTS.md section 9 entry 13: same values
# playwright_middleware.py's own DEFAULT_MAX_SCROLL_ATTEMPTS/
# DEFAULT_SCROLL_PAUSE_MS already use, for consistency -- not exposed as
# a constructor parameter (unlike timeout_ms/post_load_wait_ms) since no
# real per-target tuning need has been demonstrated yet, the same "add
# configurability only where a real gap proves it's needed" principle
# post_load_wait_ms itself was originally added under.
DEFAULT_MAX_SCROLL_ATTEMPTS = 8
DEFAULT_SCROLL_PAUSE_MS = 700
# docs/REQUIREMENTS.md section 9 entry 14 (a real, CI-confirmed gap, not
# a guess): a live CI run of the progressive-extraction path
# (progressive_extraction: true) got 20 of the expected 25 items for
# extraction_mode: parsed_html in the same run where extraction_mode:
# live_dom got the full 25 -- both use the exact same
# collect_fn/max_attempts/pause_ms contract, so the shortfall isn't a
# dedup or collection-logic bug (html_snapshot_count: 9 that run proved
# every attempt ran, no early exit). It's a real async race:
# templates/feed.html's own `loading` flag means a scroll-triggered
# `loadMore()` call is silently dropped if the *previous* one's fetch is
# still in flight when it fires -- and DEFAULT_SCROLL_PAUSE_MS (700ms,
# tuned for a plain lazy-load target's simpler DOM-append cost) doesn't
# reliably leave enough margin for one full fetch + trim round trip
# under real, sometimes-loaded CI network conditions. Progressive
# extraction is already a heavier, opt-in-only path
# (progressive_extraction: true) -- worth its own, more generous
# constants rather than tuning the shared ones and risking every other
# already-proven scroll_to_load_lazy_content caller's timing.
DEFAULT_PROGRESSIVE_MAX_SCROLL_ATTEMPTS = 10
DEFAULT_PROGRESSIVE_SCROLL_PAUSE_MS = 1_500
# docs/REQUIREMENTS.md section 9's "DOM Virtualization Instability"
# investigation: DEFAULT_PROGRESSIVE_SCROLL_PAUSE_MS above only narrowed
# a real race, never closed it -- the same test family kept failing
# intermittently (21/20/0/24 of an expected 25, across 7 separate CI
# attempts, always short, never over) even with the more generous
# constant. Root cause, confirmed against templates/feed.html's own
# source: its loadMore() silently drops a scroll-triggered call if the
# *previous* fetch is still in flight, and a fixed pause_ms sleep is a
# guessed duration for "one fetch+render+trim round trip", not a real
# completion signal -- wrong under real, variable CI load. This bounds
# how long _wait_for_network_idle below (a real completion signal) is
# allowed to wait per scroll step before falling back to the unchanged
# pause_ms sleep -- generous enough for a slow CI runner, bounded so a
# page with some unrelated persistent connection (this mock target has
# none, but _scroll.py stays engine/target-agnostic) can't hang a whole
# crawl step.
DEFAULT_PROGRESSIVE_NETWORK_IDLE_TIMEOUT_MS = 5_000


class _RawSolve(NamedTuple):
    """What a real browser-driving call actually produces."""

    url: str
    html: str
    status: int
    cookies: dict[str, str]
    # None unless extraction_selectors was given and live-DOM extraction
    # actually ran -- see Solution.items' own comment for the full
    # None-vs-list contract this mirrors exactly.
    items: list[dict[str, Any]] | None = None
    # None unless progressive_extraction was given without
    # extraction_selectors -- see Solution.html_snapshots' own comment
    # for the full contract this mirrors exactly.
    html_snapshots: list[str] | None = None


# (url, timeout_ms, post_load_wait_ms, click_selector, extraction_selectors,
# progressive_extraction, login_flow) -> raw browser result
CamoufoxSolveFn = Callable[
    [str, int, int, "str | None", "LiveDomSelectors | None", bool, "LoginFlow | None"], _RawSolve
]


# docs/REQUIREMENTS.md section 9's "DOM Virtualization Instability"
# investigation: this whole function's body needs a real, live browser
# -- it is exercised (and was always meant to be exercised) only via
# tests/integration, never tests/unit, the same reasoning entry 14's own
# CHANGELOG entry documents ("مستحيل تتعمله unit test مباشر — محتاج
# متصفح حقيقي"). The `# pragma: no cover` below makes that explicit and
# permanent instead of it silently eating into the 85% unit-coverage
# gate's margin every time this function grows (it did, twice, in this
# same investigation -- entries in CHANGELOG.md/REQUIREMENTS.md both
# record adding unrelated-but-real tests elsewhere to compensate, which
# works but isn't sustainable as a recurring fix for a gap that is
# structural, not accidental). `CamoufoxProvider.solve()` itself (the
# part that unit tests actually exercise, via the injectable `solve_fn`)
# is unaffected -- coverage there stays real and enforced.
def _default_camoufox_solve(  # pragma: no cover
    url: str,
    timeout_ms: int,
    post_load_wait_ms: int,
    click_selector: str | None = None,
    extraction_selectors: LiveDomSelectors | None = None,
    progressive_extraction: bool = False,
    login_flow: LoginFlow | None = None,
) -> _RawSolve:
    """Drive a real Camoufox browser: navigate, (optionally) click, wait
    past ``load``, read, close.

    ``click_selector`` (docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md,
    cookie-consent-wall round): since this provider drives a real
    Playwright-compatible ``Page`` directly, it can genuinely click an
    element -- unlike :class:`~src.providers.antibot.byparr_provider.ByparrProvider`,
    which structurally cannot (its own module's comment on this). No
    separate "wait after click" parameter is added: ``post_load_wait_ms``
    (already configurable) is reused for that -- it runs *after* the
    click below, so it doubles as "wait for the consent overlay to
    actually disappear" without inventing a second, overlapping knob.

    For a JSON response, reads the raw network body instead of the
    rendered DOM (``page.content()``) -- confirmed for real
    (docs/REQUIREMENTS.md section 9 entry 9) that Firefox (which this
    provider drives) wraps a raw ``application/json`` response in its
    own built-in plaintext viewer (``<html><body><pre>...`` +
    ``resource://content-accessible/plaintext.css``) before
    ``page.content()`` ever reads it, corrupting it for
    ``response.json()`` downstream.

    **Not simply ``page.goto()``'s own return value** -- a real,
    evidenced follow-up gap (docs/REQUIREMENTS.md section 9 entry 9's
    second round): for an Anubis-protected URL, that first response is
    Anubis's own interim *challenge* page (``content-type: text/html``),
    not the real target -- the actual redirect to the JSON endpoint
    happens *asynchronously, client-side*, after the challenge JS
    resolves (the exact same async-after-``load`` shape
    ``post_load_wait_ms`` itself exists to wait out, entry 4). A
    ``page.on("response", ...)`` listener tracks every main-frame
    navigation response as it happens, so the *last* one reflects the
    real, final document -- including one reached via that async
    redirect, not just the first (possibly stale) one ``goto()`` handed
    back. Every non-JSON response keeps using ``page.content()`` exactly
    as before -- this only changes behavior for the one content-type
    that gets wrapped.

    ``extraction_selectors`` (docs/REQUIREMENTS.md section 9 entry 12):
    when given, items are extracted directly from the live ``page`` (via
    :func:`~src.providers.antibot._live_dom.extract_live_dom_items`)
    *before* the browser closes below -- this is the one thing reading
    ``page.content()`` after the fact structurally cannot do, since a
    shadow root attached via ``attachShadow()`` is never included in that
    serialized string at all, regardless of which browser produced it
    (entry 11's real, confirmed gap). A no-op (``items`` stays ``None``)
    when not given, so every existing call site's behavior is unchanged.

    ``progressive_extraction`` (docs/REQUIREMENTS.md section 9 entry 14,
    the real fix for entry 13's confirmed DOM Virtualization gap): reading
    the page once, after scrolling finishes (what happens above by
    default), cannot recover content a virtualized list evicted along the
    way -- it's genuinely gone from the DOM by then. When ``True``,
    :func:`~src.providers.antibot._scroll.scroll_and_collect` extracts (or
    snapshots ``html``) after *every* scroll step instead of just the
    last, merging the results deduplicated by ``post_id`` -- via ``items``
    when ``extraction_selectors`` is also given (the "live_dom" half), or
    via ``html_snapshots`` when it isn't (the "parsed_html" half, left for
    the caller to parse and merge itself, since the live-DOM extraction
    path is the only one that needs selectors down here at all). A no-op
    (behaves exactly like ``progressive_extraction=False``) when not given.
    Each scroll step also waits (bounded,
    ``DEFAULT_PROGRESSIVE_NETWORK_IDLE_TIMEOUT_MS``) for network activity
    to genuinely settle before collecting -- see this module's own
    ``_wait_for_network_idle`` and ``_scroll.py``'s "Second revision"
    docstring for the real, CI-confirmed race this closes.

    ``login_flow`` (docs/REQUIREMENTS.md section 9 entry 15, Known
    Limitation #1: login/session): when given,
    :func:`~src.providers.antibot._login.submit_login_form` runs *first*
    -- before the ``url`` navigation below -- filling and submitting the
    real login form. The same main-frame-response tracking
    this function already needs for every other navigation reveals what
    the submit led to: a redirect that returns a non-``4xx``/``5xx``
    status is treated as success (``url`` is navigated to next, with the
    now-authenticated browser's own cookies already attached); a
    ``4xx``/``5xx`` result (wrong credentials, a stale/replayed CSRF
    token, or any other real login failure) is logged clearly and ``url``
    is *not* navigated to separately -- the login page's own failure
    response becomes what this call returns, the same "let the real
    response speak" approach the JSON/plaintext-viewer handling above
    already has. A no-op (behaves exactly like ``login_flow=None``) when
    not given.

    Raises:
        AntibotError: if the browser fails to launch, navigate, click, or
            read the page -- wraps Camoufox's own pre-launch exceptions
            and Playwright's own navigation/page errors (Camoufox's
            ``new_page()``/``goto()``/``content()`` are genuine Playwright
            calls under the hood).
    """
    from camoufox.exceptions import CamoufoxNotInstalled
    from camoufox.sync_api import Camoufox
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import Response as PlaywrightResponse

    logger = get_logger(__name__)
    # docs/REQUIREMENTS.md section 9 entry 17's monitoring-infrastructure
    # investment: off by default (None), active only when TITAN_TRACE_DIR
    # is set -- see _tracing.py's own module docstring for the full
    # reasoning.
    trace_dir = trace_dir_from_env()
    # TEMPORARY DIAGNOSTIC (docs/REQUIREMENTS.md section 9 entry 17,
    # "expand the diagnostic tool" phase -- requested after the
    # network-idle-vs-loading-flag hypothesis was falsified and the
    # separate load_more_calls=0 mystery was closed, both by real
    # evidence, not guessing). Computed here, before the browser even
    # launches, so it's always a bound local name.
    debug_loading_race = bool(os.environ.get("TITAN_DEBUG_LOADING_RACE"))
    # A *separate* gate from `debug_loading_race` (_tracing.py's own
    # module docstring explains why) -- collecting the timeline and
    # dumping it to disk are two independent decisions.
    load_event_log_dir = load_event_log_dir_from_env() if debug_loading_race else None

    def _apparmor_denial_count() -> int | None:
        """A snapshot of how many AppArmor DENIED entries for
        camoufox-bin exist in dmesg *right now* -- ``None`` if dmesg
        can't be read at all (no sudo, not Linux, ...). Called once
        before this browser session starts and once right after it
        ends; the caller logs the delta as
        ``apparmor_denials_during_solve`` (docs/REQUIREMENTS.md section
        9's monitoring-infrastructure investment, the AppArmor
        follow-up -- see _tracing.py's own module docstring). Never
        raises: a diagnostic reading that fails is a `None`, not a
        crawl failure.
        """
        import subprocess

        for cmd in (["sudo", "-n", "dmesg"], ["dmesg"]):
            try:
                result = subprocess.run(  # noqa: S603, S607
                    cmd, capture_output=True, text=True, timeout=5
                )
            except (OSError, subprocess.SubprocessError):
                continue
            if result.returncode == 0:
                return count_apparmor_camoufox_denials(result.stdout)
        return None

    apparmor_denials_before = _apparmor_denial_count()
    try:
        # camoufox ships no py.typed marker / inline stubs, so mypy sees
        # this constructor call itself (not objects it returns -- those
        # are genuine Playwright objects, typed as Any below the same
        # way playwright_middleware.py already treats them) as untyped.
        with Camoufox(headless=True) as browser:  # type: ignore[no-untyped-call]
            try:
                page = browser.new_page()
                if trace_dir is not None:
                    page.context.tracing.start(screenshots=True, snapshots=True, sources=True)
                try:
                    # Tracks the *last* main-frame navigation response,
                    # not just the first one `goto()` returns -- see this
                    # function's own docstring for why the first response
                    # alone is unreliable for an Anubis-protected URL.
                    # `is_navigation_request()` is required, not just
                    # `resp.frame is page.main_frame` -- confirmed for
                    # real (docs/REQUIREMENTS.md section 9 entry 9's
                    # third round) that a plain frame check also matches
                    # ordinary fetch/XHR calls a page's own JS makes
                    # (e.g. Anubis's own pass-challenge API call, which
                    # returns JSON) -- those have the same `.frame` as a
                    # real navigation but are not one, and one such call
                    # was overwriting the real page's response entirely.
                    last_main_frame_response: PlaywrightResponse | None = None

                    def _track_main_frame_response(resp: PlaywrightResponse) -> None:
                        nonlocal last_main_frame_response
                        if resp.frame is page.main_frame and resp.request.is_navigation_request():
                            last_main_frame_response = resp

                    page.on("response", _track_main_frame_response)
                    # entry 15: login runs first -- see this function's
                    # own docstring for the full success/failure
                    # decision. `login_flow` is a no-op branch when not
                    # given, same shape as every other optional
                    # capability here.
                    if login_flow is not None:
                        login_ok, final_status = perform_login_and_navigate(
                            page,
                            login_flow.login_url,
                            login_flow.username,
                            login_flow.password,
                            login_flow.username_field,
                            login_flow.password_field,
                            login_flow.submit_selector,
                            timeout_ms,
                            post_load_wait_ms,
                            lambda: (
                                last_main_frame_response.status
                                if last_main_frame_response is not None
                                else None
                            ),
                            url,
                            login_flow.session_expiry_probe_url,
                        )
                        log_login_outcome(
                            logger,
                            "camoufox_provider",
                            login_flow.login_url,
                            url,
                            login_ok,
                            final_status,
                        )
                        initial_response = last_main_frame_response
                    else:
                        initial_response = page.goto(url, timeout=timeout_ms)
                    if click_selector:
                        page.click(click_selector, timeout=timeout_ms)
                    # The one thing ByparrProvider structurally cannot
                    # do: hold the browser open past `load` so async,
                    # post-load challenge JS (e.g. Anubis's real PoW
                    # flow) gets a real chance to finish -- or, if a
                    # click just happened above, give whatever it
                    # triggered (e.g. a consent-wall reload) time to
                    # settle before reading content.
                    page.wait_for_timeout(post_load_wait_ms)
                    # docs/REQUIREMENTS.md section 9 entry 13: scrolled
                    # *after* the wait above, not before -- real content
                    # needs to actually be there first (past Anubis/the
                    # cookie wall) for there to be anything lazy-loading
                    # JS could load more of. Harmless on a page with no
                    # infinite scroll at all (src.providers.antibot._scroll's
                    # own docstring) -- called unconditionally, same
                    # justification `render_with_playwright` already has.
                    #
                    # entry 14: progressive_extraction branches to
                    # scroll_and_collect instead, extracting/snapshotting
                    # after every step rather than reading only once at
                    # the end -- see this function's own docstring.
                    items: list[dict[str, Any]] | None = None
                    html_snapshots: list[str] | None = None
                    network_idle_timeouts = 0
                    if progressive_extraction:
                        # docs/REQUIREMENTS.md section 9's "DOM
                        # Virtualization Instability" investigation,
                        # second attempt: the first fix here used
                        # page.wait_for_load_state("networkidle"), and a
                        # real CI run (32973393111) showed it made *no*
                        # measurable difference -- same exact shortfall
                        # (20 of 25) as before it existed. Root cause,
                        # confirmed against Playwright's own load-state
                        # tracking: "networkidle" is a per-*navigation*
                        # lifecycle flag -- once reached (which happens
                        # almost immediately after the very first,
                        # automatic on-load batch here), every later call
                        # to wait_for_load_state("networkidle") resolves
                        # *immediately* without waiting for anything,
                        # since no new navigation ever happens between
                        # scroll steps on this page (it's all in-page
                        # AJAX). It is simply the wrong tool for
                        # resynchronizing against a *repeated* same-page
                        # fetch. This tracks in-flight requests directly
                        # instead (page.on("request"/"requestfinished"/
                        # "requestfailed")), maintained continuously
                        # across the whole progressive collection (not
                        # reconstructed per step, so it can't miss a
                        # request that starts and finishes between two
                        # separate calls) -- a real, live "is anything
                        # actually in flight right now" signal, correctly
                        # re-armed on every check. RequestCounter and
                        # poll_until_idle (_scroll.py) are pure,
                        # unit-tested functions -- only this listener
                        # wiring, which needs a real Page, lives here
                        # untested (same "extract what can be tested"
                        # principle entry 14 already established for this
                        # module).
                        request_counter = RequestCounter()

                        def _read_feed_attr(attr: str) -> str | None:
                            """Reads one of templates/feed.html's own
                            `container` diagnostic attributes
                            (``data-load-more-calls``/``data-load-more-dropped``/
                            ``data-loading-flag``) -- a DOM attribute,
                            not a ``window.*`` property. **Real, CI-confirmed
                            fix (docs/REQUIREMENTS.md section 9 entry
                            17's `load_more_calls`/`load_more_dropped`
                            mystery):** these were originally plain
                            ``window.__loadMoreCalls``/``__loadMoreDropped``
                            expando properties -- confirmed by hand
                            (three separate control cases: a fresh
                            ``about:blank`` page, a synthetic
                            ``page.set_content()`` page, and this exact
                            live page) that Camoufox/Firefox's
                            automation protocol cannot see a plain
                            ``window.*`` property once it is set by the
                            *page's own* inline ``<script>`` execution
                            -- ``page.evaluate()`` reads it back as
                            ``undefined`` every single time (silently
                            coerced to a misleadingly plausible-looking
                            ``0`` by the old ``|| 0`` fallback), even
                            though ``loadMore()`` itself was running and
                            appending real content the whole time
                            (posts kept arriving; the counter alone was
                            unreadable). The same three controls
                            confirmed a DOM *attribute* set by that same
                            inline script reads back correctly -- this
                            is specifically a ``window`` expando-property
                            isolation (almost certainly Firefox's
                            Xray-vision wrapper separating the page's
                            own script realm from the
                            automation-privileged one ``evaluate()``
                            runs in), not a DOM-content isolation.
                            ``None`` on any page that isn't this one (no
                            matching element at all) -- the same
                            harmless fallback shape the old ``|| 0`` had.
                            """
                            return page.evaluate(  # type: ignore[no-any-return]
                                "document.querySelector('[data-role=\"feed\"]')"
                                f"?.getAttribute('{attr}') ?? null"
                            )

                        # TEMPORARY DIAGNOSTIC (docs/REQUIREMENTS.md
                        # section 9 entry 17, unconfirmed hypothesis
                        # review requested before any third fix attempt):
                        # off by default (TITAN_DEBUG_LOADING_RACE unset
                        # -- zero behavior/timing change for every
                        # existing caller, same pattern as
                        # TITAN_TRACE_DIR/_tracing.py). When set, samples
                        # templates/feed.html's own ``data-loading-flag``
                        # attribute (a direct mirror of the page's real
                        # `loading` closure variable, added purely for
                        # this investigation) at the *exact instant*
                        # poll_until_idle reports the network settled --
                        # answering with direct evidence, not another
                        # guess, whether "network idle" (what
                        # RequestCounter/poll_until_idle track) and "the
                        # page's own loadMore().then() callback has
                        # actually finished and reset `loading`" are
                        # really two different completion signals that
                        # can be observed apart. ``debug_loading_race``
                        # itself is computed once, right after
                        # ``browser.new_page()`` above (needed there
                        # too, to gate ``page.expose_function()`` before
                        # any navigation) -- reused here unchanged.
                        loading_flag_samples: list[bool] = []
                        # TEMPORARY DIAGNOSTIC, same review: samples
                        # data-load-more-calls itself at every settle_fn
                        # call (not just once at the very end) -- direct
                        # evidence for whether it is ever nonzero *during*
                        # the run (and something later resets it back to
                        # 0 before the final read) or whether it never
                        # leaves 0 at all, which would point at the
                        # counter/increment itself, not at a late reset.
                        load_more_calls_samples: list[int] = []
                        # TEMPORARY DIAGNOSTIC, follow-up to the same
                        # entry-17 review: load_more_calls/load_more_dropped
                        # reading 0/0 on *every* local repro run so far
                        # (including two clean 25/25 passes) is itself
                        # unexplained -- loadMore()'s own automatic
                        # first call increments __loadMoreCalls
                        # synchronously, before any fetch, so it should
                        # never read back 0 if the same document that ran
                        # it is still the one page.evaluate() reads from
                        # at the end. Counts real main-frame navigations
                        # during the progressive-collection window itself
                        # -- direct evidence for/against a hidden
                        # mid-collection reset (e.g. a second,
                        # later-than-expected Anubis redirect) silently
                        # replacing the document (and therefore its
                        # window state) partway through, which the
                        # existing one-navigation assumption baked into
                        # this whole block never checks for.
                        main_frame_navigations_during_progressive = 0

                        def _count_main_frame_navigation(frame: Any) -> None:
                            nonlocal main_frame_navigations_during_progressive
                            if debug_loading_race and frame == page.main_frame:
                                main_frame_navigations_during_progressive += 1

                        if debug_loading_race:
                            page.on("framenavigated", _count_main_frame_navigation)

                        def _wait_for_network_idle(timeout_ms: int) -> None:
                            nonlocal network_idle_timeouts
                            settled = poll_until_idle(
                                request_counter.is_idle, page.wait_for_timeout, timeout_ms
                            )
                            if not settled:
                                network_idle_timeouts += 1
                            elif debug_loading_race:
                                loading_flag_samples.append(
                                    _read_feed_attr("data-loading-flag") == "true"
                                )
                                load_more_calls_samples.append(
                                    int(_read_feed_attr("data-load-more-calls") or 0)
                                )

                        page.on("request", request_counter.on_start)
                        page.on("requestfinished", request_counter.on_settle)
                        page.on("requestfailed", request_counter.on_settle)
                        try:
                            if extraction_selectors is not None:
                                items = collect_live_dom_items_progressively(
                                    page,
                                    extraction_selectors.item,
                                    extraction_selectors.fields,
                                    DEFAULT_PROGRESSIVE_MAX_SCROLL_ATTEMPTS,
                                    DEFAULT_PROGRESSIVE_SCROLL_PAUSE_MS,
                                    settle_fn=lambda: _wait_for_network_idle(
                                        DEFAULT_PROGRESSIVE_NETWORK_IDLE_TIMEOUT_MS
                                    ),
                                )
                            else:
                                html_snapshots = collect_html_snapshots(
                                    page,
                                    DEFAULT_PROGRESSIVE_MAX_SCROLL_ATTEMPTS,
                                    DEFAULT_PROGRESSIVE_SCROLL_PAUSE_MS,
                                    settle_fn=lambda: _wait_for_network_idle(
                                        DEFAULT_PROGRESSIVE_NETWORK_IDLE_TIMEOUT_MS
                                    ),
                                )
                        finally:
                            page.remove_listener("request", request_counter.on_start)
                            page.remove_listener("requestfinished", request_counter.on_settle)
                            page.remove_listener("requestfailed", request_counter.on_settle)
                            if debug_loading_race:
                                page.remove_listener(
                                    "framenavigated", _count_main_frame_navigation
                                )
                    else:
                        scroll_to_load_lazy_content(
                            page, DEFAULT_MAX_SCROLL_ATTEMPTS, DEFAULT_SCROLL_PAUSE_MS
                        )
                        if extraction_selectors is not None:
                            items = extract_live_dom_items(
                                page, extraction_selectors.item, extraction_selectors.fields
                            )
                    # docs/REQUIREMENTS.md section 9's "DOM Virtualization
                    # Instability" investigation, monitoring-infrastructure
                    # investment: data-load-more-calls/data-load-more-dropped
                    # (templates/feed.html's `container` attributes --
                    # see _read_feed_attr's own docstring above for why
                    # these are DOM attributes, not `window.*`
                    # properties, after a real, CI-confirmed bug in the
                    # original window-property version) directly answer
                    # "did the page's own loadMore() guard actually drop
                    # a call", something network_idle_timeouts above
                    # cannot -- confirmed for real (CI run 32997246624)
                    # that a network-side wait succeeding on every poll
                    # does *not* rule out a page-side drop. Harmless
                    # (reads back 0/0) on any page that isn't this one.
                    load_more_calls = (
                        int(_read_feed_attr("data-load-more-calls") or 0)
                        if progressive_extraction
                        else None
                    )
                    load_more_dropped = (
                        int(_read_feed_attr("data-load-more-dropped") or 0)
                        if progressive_extraction
                        else None
                    )
                    # TEMPORARY DIAGNOSTIC, same entry 17 review as above:
                    # `None` (not logged as a list) unless
                    # TITAN_DEBUG_LOADING_RACE was set for this solve --
                    # avoids a meaningless always-empty field on every
                    # normal run.
                    loading_flag_at_network_idle = (
                        loading_flag_samples
                        if progressive_extraction and debug_loading_race
                        else None
                    )
                    main_frame_navigations_during_progressive_result = (
                        main_frame_navigations_during_progressive
                        if progressive_extraction and debug_loading_race
                        else None
                    )
                    load_more_calls_samples_result = (
                        load_more_calls_samples
                        if progressive_extraction and debug_loading_race
                        else None
                    )
                    # TEMPORARY DIAGNOSTIC (entry 17's "expand the
                    # diagnostic tool" phase -- corrected approach, see
                    # templates/feed.html's own comment for the full
                    # story of why `page.expose_function()`, the first
                    # attempt, didn't work): `data-load-event-log` is a
                    # single JSON-encoded DOM attribute
                    # (`container.setAttribute`) the page's own script
                    # updates on every `loadMore()` checkpoint
                    # (enter/blocked/fetch_start/fetch_done/
                    # reset_loading), read *once* here, after
                    # progressive collection is fully done -- not
                    # per-event, so this read itself can never perturb
                    # the timing it's reporting on. Malformed/missing
                    # JSON (any page that isn't this one, or a solve
                    # that errored before the script ever ran) yields an
                    # empty list, not a crash -- this is a diagnostic,
                    # never allowed to fail the actual crawl.
                    load_event_log: list[dict[str, Any]] = []
                    if progressive_extraction and debug_loading_race:
                        raw_load_event_log = _read_feed_attr("data-load-event-log")
                        if raw_load_event_log:
                            try:
                                parsed_load_event_log = json.loads(raw_load_event_log)
                            except (ValueError, TypeError):
                                parsed_load_event_log = []
                            if isinstance(parsed_load_event_log, list):
                                load_event_log = parsed_load_event_log
                    load_event_log_path = (
                        build_load_event_log_path(load_event_log_dir, url, "camoufox")
                        if load_event_log_dir is not None and load_event_log
                        else None
                    )
                    if load_event_log_path is not None:
                        Path(load_event_log_path).write_text(render_load_event_log(load_event_log))
                    final_response = last_main_frame_response or initial_response
                    content_type = (
                        final_response.headers.get("content-type", "")
                        if final_response is not None
                        else ""
                    )
                    if "application/json" in content_type:
                        # The raw network body of the *real, final*
                        # response -- sidesteps Firefox's own built-in
                        # plaintext/JSON viewer wrapping the rendered DOM
                        # (see this function's docstring).
                        html = (
                            final_response.text()
                            if final_response is not None
                            else page.content()
                        )
                    else:
                        html = page.content()
                    # entry 14: the last snapshot is the freshest read of
                    # the page either way -- overrides the content-type
                    # -based read above only when progressive parsed_html
                    # collection actually ran.
                    if html_snapshots:
                        html = html_snapshots[-1]
                    status = final_response.status if final_response is not None else 200
                    cookies = {c["name"]: c["value"] for c in page.context.cookies()}
                    # docs/REQUIREMENTS.md section 9's "DOM Virtualization
                    # Instability" investigation, the AppArmor follow-up:
                    # a real, user-requested quantitative link between
                    # this specific solve's AppArmor denial count and
                    # whether *this* solve hit the race -- see
                    # _apparmor_denial_count's own docstring and
                    # _tracing.py's module docstring for the full
                    # reasoning. `None` (not 0) when dmesg couldn't be
                    # read at all, so a genuine zero-denials solve is
                    # never confused with "couldn't check".
                    apparmor_denials_during_solve = apparmor_denial_delta(
                        apparmor_denials_before, _apparmor_denial_count()
                    )
                    # Always logged (not just on failure): the one piece of
                    # evidence that actually distinguishes "got real
                    # content" from "still stuck on a challenge/interstitial
                    # page" -- status 200 alone means nothing for a
                    # provider that solves anti-bot challenges (a
                    # challenge/deny page is routinely served as a normal
                    # 200), and this is genuinely useful after the fact
                    # (e.g. in a live-test's captured subprocess output),
                    # not just while debugging this one provider by hand.
                    logger.info(
                        "camoufox_provider.solved",
                        extra={
                            "url": url,
                            "final_url": page.url,
                            "status": status,
                            "title": page.title(),
                            "html_length": len(html),
                            "cookie_names": sorted(cookies),
                            "click_selector": click_selector,
                            "content_type": content_type,
                            "used_raw_network_body": "application/json" in content_type,
                            "live_dom_extraction_used": items is not None,
                            "live_dom_item_count": len(items) if items is not None else None,
                            "progressive_extraction": progressive_extraction,
                            "html_snapshot_count": (
                                len(html_snapshots) if html_snapshots is not None else None
                            ),
                            "login_flow_used": login_flow is not None,
                            # docs/REQUIREMENTS.md section 9's "DOM
                            # Virtualization Instability" investigation:
                            # how many of this solve's settle_fn calls
                            # hit DEFAULT_PROGRESSIVE_NETWORK_IDLE_TIMEOUT_MS
                            # without ever seeing the network go quiet --
                            # 0 when progressive_extraction is False (the
                            # settle_fn/counter never runs at all). Real
                            # diagnostic evidence for whether this
                            # mechanism is doing anything, instead of
                            # having to guess from the item count alone
                            # (the mistake the first, wait_for_load_state
                            # -based attempt at this fix made).
                            "network_idle_timeouts": network_idle_timeouts,
                            "load_more_calls": load_more_calls,
                            "load_more_dropped": load_more_dropped,
                            "apparmor_denials_during_solve": apparmor_denials_during_solve,
                            # TEMPORARY DIAGNOSTIC (entry 17 hypothesis
                            # review, TITAN_DEBUG_LOADING_RACE-gated):
                            # `data-loading-flag`'s value sampled
                            # at every settle_fn call that reported
                            # network-idle, in order. Any `true` in this
                            # list is direct, in-the-act evidence that
                            # "network idle" and "loadMore()'s `loading`
                            # flag actually reset" are observably
                            # different instants -- not inferred from the
                            # final item count.
                            "loading_flag_at_network_idle": loading_flag_at_network_idle,
                            "main_frame_navigations_during_progressive": (
                                main_frame_navigations_during_progressive_result
                            ),
                            "load_more_calls_samples": load_more_calls_samples_result,
                            # TEMPORARY DIAGNOSTIC (entry 17's "expand the
                            # diagnostic tool" phase): a summary, not the
                            # full timeline (which can be long) -- the
                            # complete per-event data lives in
                            # `load_event_log_path`'s file when
                            # TITAN_LOAD_EVENT_LOG_DIR is set, or nowhere
                            # (still collected in memory, just not
                            # persisted) if that dir isn't configured.
                            "load_event_count": (
                                len(load_event_log) if debug_loading_race else None
                            ),
                            "load_event_log_path": load_event_log_path,
                        },
                    )
                    return _RawSolve(
                        url=page.url,
                        html=html,
                        status=status,
                        cookies=cookies,
                        items=items,
                        html_snapshots=html_snapshots,
                    )
                finally:
                    if trace_dir is not None:
                        page.context.tracing.stop(path=build_trace_path(trace_dir, url, "camoufox"))
                    page.close()
            finally:
                browser.close()
    except CamoufoxNotInstalled as exc:
        raise AntibotError(
            f"camoufox browser binary not installed for {url} "
            "(run: python -m camoufox fetch)"
        ) from exc
    except PlaywrightError as exc:
        # docs/REQUIREMENTS.md section 9's "DOM Virtualization
        # Instability" investigation, the AppArmor follow-up: this is
        # exactly the branch a real spontaneous browser crash
        # (TargetClosedError) goes through -- the "solved" log line
        # above never gets a chance to run, so the same
        # apparmor_denials_during_solve evidence is logged here too,
        # on the one path where it matters most.
        apparmor_denials_during_solve = apparmor_denial_delta(
            apparmor_denials_before, _apparmor_denial_count()
        )
        # Entry 17's "expand the diagnostic tool" phase load-event
        # timeline is deliberately *not* logged on this path: it's read
        # from the page's own DOM (`_read_feed_attr`) only once,
        # normally, right before the "solved" log line below -- exactly
        # the read this crash means never got a chance to happen
        # (`page`/the browser itself may already be gone by the time
        # this branch runs). Nothing meaningful to report here, so
        # nothing invented.
        logger.error(
            "camoufox_provider.solve_crashed",
            extra={"url": url, "apparmor_denials_during_solve": apparmor_denials_during_solve},
        )
        raise AntibotError(f"camoufox failed to solve {url}: {exc}") from exc


class CamoufoxProvider(AntibotProvider):
    """Solves anti-bot challenges by driving a real Camoufox browser directly."""

    def __init__(
        self,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        post_load_wait_ms: int = DEFAULT_POST_LOAD_WAIT_MS,
        solve_fn: CamoufoxSolveFn | None = None,
        logger: Logger | None = None,
    ) -> None:
        if timeout_ms <= 0:
            raise AntibotError(f"timeout_ms must be > 0, got {timeout_ms}")
        if post_load_wait_ms < 0:
            raise AntibotError(f"post_load_wait_ms must be >= 0, got {post_load_wait_ms}")
        self._timeout_ms = timeout_ms
        self._post_load_wait_ms = post_load_wait_ms
        self._solve_fn = solve_fn or _default_camoufox_solve
        self.logger = logger or get_logger(__name__)

    def solve(
        self,
        url: str,
        click_selector: str | None = None,
        extraction_selectors: LiveDomSelectors | None = None,
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
    ) -> Solution:
        try:
            raw = self._solve_fn(
                url,
                self._timeout_ms,
                self._post_load_wait_ms,
                click_selector,
                extraction_selectors,
                progressive_extraction,
                login_flow,
            )
        except AntibotError:
            self.logger.error("camoufox_provider.solve_failed", extra={"url": url})
            raise

        return Solution(
            url=raw.url,
            html=raw.html,
            status_code=raw.status,
            cookies=raw.cookies,
            items=raw.items,
            html_snapshots=raw.html_snapshots,
            solved_at=datetime.now(tz=UTC),
        )
