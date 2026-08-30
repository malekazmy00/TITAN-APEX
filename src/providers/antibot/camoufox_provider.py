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

from src.core.exceptions import AntibotError, BrowserCrashedError
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
from src.providers.antibot._mouse_movement import (
    move_mouse_along_path,
    oxymouse_path_generator,
)
from src.providers.antibot._scroll import (
    collect_html_snapshots,
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
from src.providers.antibot.cookie_jar_manager import load_accumulated_state, record_new_session

DEFAULT_TIMEOUT_MS = 30_000
# docs/REQUIREMENTS.md section 9 entry 17: the accepted mitigation for a
# real, kernel-log-confirmed Firefox engine segfault (BrowserCrashedError's
# own docstring has the full evidence) that has no known upstream fix --
# retry the *whole* solve on a fresh browser instance (the crash is in
# the engine itself, not this project's own logic, so nothing about a
# fresh instance would carry the same fault forward) up to this many
# total attempts. 3 matches this project's own established "bounded,
# not open-ended retry" convention elsewhere (e.g. entry 17's own
# consecutive-scroll-stall tolerance).
DEFAULT_MAX_BROWSER_CRASH_ATTEMPTS = 3
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
# docs/REQUIREMENTS.md section 9 entry 17's "Seventh revision": the same
# selector _read_feed_attr's own JS strings already hardcode (as a
# querySelector() string there, not reusable directly) -- named here as
# a real Python constant so scroll_and_collect's hover_fn (see
# _scroll.py's own module docstring) targets exactly the same element
# every other progressive-collection diagnostic in this file already
# reads from.
_FEED_CONTAINER_SELECTOR = '[data-role="feed"]'
# docs/REQUIREMENTS.md section 9 entry 17's "Eighth revision": a short,
# fail-fast timeout for the per-attempt hover actionability probe --
# 3000ms is a commonly documented duration for this kind of quick
# actionability check (not this project's own invented number), well
# short of Locator.hover()'s own 30000ms default, which real CI evidence
# (run 33275376646) confirmed can block the *entire* solve for 30 real
# seconds before raising, once an unhandled interstitial overlay is
# genuinely intercepting pointer events over the container.
DEFAULT_PROGRESSIVE_HOVER_TIMEOUT_MS = 3_000
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
# docs/REQUIREMENTS.md section 9 entry 17's "Sixth revision": a single
# page.expect_response() timeout is *not* a reliable "pagination has
# ended" signal on its own -- confirmed for real that page.mouse.wheel()
# itself intermittently fails to produce any scroll event at all, for
# reasons unrelated to how much content is actually left (the earlier
# "no scroll room" root cause is fixed separately, by
# templates/feed.html's own virtualization-spacer). Tolerating a run of
# consecutive timeouts before giving up (rather than the very first one)
# keeps that resilience without ever burning through *all* of
# DEFAULT_PROGRESSIVE_MAX_SCROLL_ATTEMPTS on a target that still has
# real, unread pages left. Only used as a fallback when the real
# `/api/feed` response body's own `page_info.has_next_page` -- the
# authoritative signal, preferred whenever a response actually arrives
# at all -- doesn't say either way (a timeout means no response arrived
# to read it from in the first place).
DEFAULT_PROGRESSIVE_MAX_CONSECUTIVE_SCROLL_STALLS = 3
# docs/REQUIREMENTS.md section 9 entry 21, Step 2: one shared, project-
# -wide default -- deliberately *not* per-target -- so the accumulated
# jar naturally mixes cookies from every real target this project ever
# solves, not just one (see cookie_jar_manager.py's own module docstring
# for why that's exactly the "organic, unrelated-site cookies" property
# this entry's own design asked for, with zero extra code needed for it
# specifically). "var/" matches this project's own existing convention
# for local, non-source runtime state (Anubis's own botPolicy.yaml
# comments its own optional honeypot IP log at "./var/honeypot.addrs").
DEFAULT_COOKIE_JAR_PATH = "var/cookie_jar.json"


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
# progressive_extraction, login_flow, warm_session_urls,
# use_accumulated_profile, cookie_jar_path) -> raw browser result
CamoufoxSolveFn = Callable[
    [
        str,
        int,
        int,
        "str | None",
        "LiveDomSelectors | None",
        bool,
        "LoginFlow | None",
        "list[str] | None",
        bool,
        str,
    ],
    _RawSolve,
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
def _classify_solve_exception(
    browser_crashed: bool, url: str, exc: Exception
) -> AntibotError:
    """docs/REQUIREMENTS.md section 9 entry 17: the one piece of the
    crash-recovery logic that's genuinely pure (no real browser/page
    object involved) and independently unit-tested here, deliberately
    pulled *out* of :func:`_default_camoufox_solve`'s own
    ``# pragma: no cover`` body -- the same reason
    ``randomized_scroll_delta``/``count_apparmor_camoufox_denials`` live
    as standalone functions instead of inline: this is the actual
    decision (did ``page.on("crash")``/``browser.on(...)`` really fire?)
    a test can exercise directly with a plain ``bool``, without needing
    to fake an entire browser session.

    Returns (not raises) so a caller decides when/whether to actually
    raise it (and can attach ``from exc`` at that point) --
    :class:`~src.core.exceptions.BrowserCrashedError` only when
    ``browser_crashed`` is ``True``, plain
    :class:`~src.core.exceptions.AntibotError` otherwise.
    """
    if browser_crashed:
        return BrowserCrashedError(
            f"camoufox's browser engine crashed mid-solve for {url}: {exc}"
        )
    return AntibotError(f"camoufox failed to solve {url}: {exc}")


def _default_camoufox_solve(  # pragma: no cover
    url: str,
    timeout_ms: int,
    post_load_wait_ms: int,
    click_selector: str | None = None,
    extraction_selectors: LiveDomSelectors | None = None,
    progressive_extraction: bool = False,
    login_flow: LoginFlow | None = None,
    warm_session_urls: list[str] | None = None,
    use_accumulated_profile: bool = False,
    cookie_jar_path: str = DEFAULT_COOKIE_JAR_PATH,
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

    ``warm_session_urls`` (docs/REQUIREMENTS.md section 9 entry 21, Step
    2): when given, ``page.goto()`` visits each URL, in order, *before*
    ``login_flow``/``url`` itself -- all on this exact same ``page``, so
    any cookies a warm-up page sets are already present in this
    browser's cookie jar by the time the real navigation happens (a real
    browser context naturally carries cookies across navigations on the
    same page; nothing else needs to be done here to make that true).
    This is the actual fix for the gap Step 1 (entry 21) documented and
    left open: a warm-up chain built purely at the Scrapy/GenericSpider
    level never reached here at all, since every ``solve()`` call
    launches its own independent browser with no connection to any
    other Scrapy request's own headers/cookies. A no-op (behaves exactly
    like ``warm_session_urls=None``) when not given, or given empty.

    ``use_accumulated_profile``/``cookie_jar_path`` (docs/REQUIREMENTS.md
    section 9 entry 21, Step 2): when ``use_accumulated_profile`` is
    ``True``, the browser context this whole function drives (not just
    ``page``, since ``storage_state`` is a context-creation-time option)
    is created from whatever cookies/storage
    :func:`~src.providers.antibot.cookie_jar_manager.load_accumulated_state`
    finds already saved at ``cookie_jar_path`` -- real state from many
    *separate*, earlier ``solve()`` calls, not just this one. On a
    successful solve, this call's own final
    ``context.storage_state()`` is saved back via
    :func:`~src.providers.antibot.cookie_jar_manager.record_new_session`,
    so the *next* call (this target's or any other's -- the jar is
    shared project-wide, not per-target, see that module's own module
    docstring for why that's deliberate) starts from an even more
    "lived-in" profile. ``False`` (the default) keeps every existing
    caller's exact prior behavior: a genuinely fresh, empty browser
    profile every single call, with nothing read from or written to
    ``cookie_jar_path`` at all -- the same complete isolation entry 17's
    own test suite depends on.

    Raises:
        AntibotError: if the browser fails to launch, navigate, click, or
            read the page -- wraps Camoufox's own pre-launch exceptions
            and Playwright's own navigation/page errors (Camoufox's
            ``new_page()``/``goto()``/``content()`` are genuine Playwright
            calls under the hood).
    """
    from camoufox.exceptions import CamoufoxNotInstalled
    from camoufox.sync_api import Camoufox
    from playwright.sync_api import Browser as PlaywrightBrowser
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import Response as PlaywrightResponse
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

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
            # docs/REQUIREMENTS.md section 9 entry 17's real-CI-confirmed
            # finding: an occasional genuine Firefox engine segfault
            # (dmesg's own "DOM Worker[...]: segfault ... in libxul.so",
            # a long-standing, unresolved-upstream class of Firefox crash
            # -- see BrowserCrashedError's own docstring) previously
            # surfaced only as a generic PlaywrightError ("Target page,
            # context or browser has been closed") from whatever call
            # happened to be in flight when it hit -- a real crash,
            # correctly classified as *some* AntibotError, but never
            # distinguishable from any other solve failure.
            # `browser_crashed` is bound *before* `browser.new_page()`
            # (not "later") specifically so it stays defined even if that
            # very call is what the crash interrupts -- the exception
            # handler at the bottom of this function reads it regardless
            # of exactly where a crash happened to hit.
            browser_crashed = False

            def _mark_browser_crashed(*_args: object) -> None:
                nonlocal browser_crashed
                browser_crashed = True

            # Camoufox's own context manager can hand back either a
            # genuine multi-context Browser or a persistent
            # BrowserContext depending on its own launch mode (confirmed
            # by mypy itself: `reveal_type(browser)` here resolves to
            # `Browser | BrowserContext`, not one fixed type) -- their
            # disconnection events have different names ("disconnected"
            # vs. "close"), so this checks which one it actually got
            # instead of assuming.
            if isinstance(browser, PlaywrightBrowser):
                browser.on("disconnected", _mark_browser_crashed)
                # ignore_https_errors=True (docs/REQUIREMENTS.md section
                # 9, JA4/TLS experiment -- ported from claude/ja4-experiment
                # onto this branch, entry 17's own follow-up): unconditional,
                # same justification generic_spider.py's own
                # handle_httpstatus_list already has -- this only matters
                # when a real TLS handshake actually happens. Every
                # existing plain-http:// target here negotiates no TLS at
                # all, so this is a genuine no-op for them; it only takes
                # effect against the JA4-proxy target's self-signed cert.
                #
                # docs/REQUIREMENTS.md section 9 entry 21, Step 2:
                # `storage_state` is a real, documented context-creation
                # -time-only option (there's no separate "load state into
                # an already-open context" call) -- confirmed directly,
                # both against Playwright's own docs and by hand against
                # a real Camoufox session (save, then reload in a
                # completely separate browser instance -- even a session
                # cookie with no explicit expiry survived the round
                # trip). `None` (`use_accumulated_profile=False`, or
                # `True` but no jar saved yet) behaves identically to
                # never passing `storage_state` at all -- Playwright's
                # own real default, a genuinely fresh, empty profile.
                loaded_state = (
                    load_accumulated_state(cookie_jar_path) if use_accumulated_profile else None
                )
                context = browser.new_context(
                    ignore_https_errors=True, storage_state=loaded_state  # type: ignore[arg-type]
                )
            else:
                browser.on("close", _mark_browser_crashed)
                # docs/REQUIREMENTS.md section 9 entry 21, Step 2: a
                # persistent-context launch (never actually exercised by
                # this project's own Camoufox(headless=True) call --
                # confirmed by entry 17's own investigation: `browser` is
                # always the concrete `Browser` type at runtime here, not
                # this branch) has no equivalent "create with
                # storage_state" entry point at all -- the real,
                # confirmed limitation GitHub issue
                # microsoft/playwright#36139 documents is specifically
                # about this launch mode (`launch_persistent_context`/
                # `user_data_dir`), not about `storage_state()` itself
                # (this entry's own correction: the issue's own reporter
                # uses `storage_state()` as the *working fix* for it).
                # `use_accumulated_profile` is a documented no-op here
                # rather than a silently-partial application.
                context = browser
            try:
                page = context.new_page()
                page.on("crash", _mark_browser_crashed)
                if trace_dir is not None:
                    page.context.tracing.start(screenshots=True, snapshots=True, sources=True)
                # `None` for the very first navigation (a real cold
                # start has no Referer either -- Scrapfly's own source,
                # entry 21's own citation, is explicit that this is
                # normal) -- set to each warm-up hop's own URL as the
                # loop advances, then used as the real `url` navigation's
                # own Referer below, so the *whole* chain (not just
                # cookies) matches what Step 1's Scrapy-level version
                # already gets for free from RefererMiddleware.
                last_warm_session_url: str | None = None
                if warm_session_urls:
                    # docs/REQUIREMENTS.md section 9 entry 21, Step 2:
                    # every one of these navigations shares this exact
                    # page/context, so any cookie a warm-up page sets is
                    # already in this browser's real cookie jar by the
                    # time the actual login_flow/url navigation below
                    # happens -- a real browser context naturally carries
                    # cookies across navigations on the same page, so
                    # nothing else needs to be done here to make that
                    # true. The real fix for the gap Step 1 (entry 21)
                    # documented and left open: a warm-up chain built
                    # purely at the Scrapy/GenericSpider level never
                    # reached here at all, since every solve() call
                    # launches its own independent browser with no
                    # connection to any other Scrapy request's own
                    # headers/cookies.
                    #
                    # `referer=` is passed explicitly here -- confirmed
                    # directly against Playwright's own source
                    # (`Page.goto`'s real signature): unlike cookies,
                    # ``page.goto()`` never automatically derives Referer
                    # from whatever the same page navigated to
                    # previously (each call is like a URL typed directly,
                    # not a followed link) -- without this, the warm-up
                    # chain would still connect cookies but silently
                    # produce no real Referer chain at all, missing half
                    # of what Step 1 (entry 21) already established.
                    for warm_url in warm_session_urls:
                        page.goto(warm_url, timeout=timeout_ms, referer=last_warm_session_url)
                        page.wait_for_timeout(post_load_wait_ms)
                        last_warm_session_url = warm_url
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
                        # docs/REQUIREMENTS.md section 9 entry 21, Step
                        # 2: the real target's own Referer, completing
                        # the chain the warm-up loop above started --
                        # `None` (this function's own prior, unchanged
                        # behavior) when no warm-up ran at all.
                        initial_response = page.goto(
                            url, timeout=timeout_ms, referer=last_warm_session_url
                        )
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
                    progressive_scroll_ended_early: bool | None = None
                    if progressive_extraction:
                        # docs/REQUIREMENTS.md section 9's "DOM
                        # Virtualization Instability" investigation --
                        # **Fifth revision (this version), replacing the
                        # network-idle-polling approach above entirely,
                        # not just narrowing it:** the "Second"/"Third"
                        # revisions (page.wait_for_load_state, then
                        # RequestCounter/poll_until_idle) both shared one
                        # real, root-caused flaw: they *register the
                        # wait after the scroll trigger has already run*
                        # (settle_fn was called *following*
                        # _SCROLL_AND_DISPATCH_SCRIPT/page.mouse.wheel(),
                        # never wrapping it). A source documenting
                        # Playwright's own "trigger-and-wait" pattern for
                        # exactly this shape of problem (testing
                        # infinite-scroll/lazy-loading) is explicit that
                        # this ordering is itself a race whenever the
                        # response is fast: nothing guarantees the
                        # listener is armed before the response the
                        # trigger caused already arrived. Confirmed for
                        # real (this entry's own "Fourth revision" local
                        # investigation): a real page.mouse.wheel() call
                        # can itself produce more than one genuine
                        # scroll-triggered loadMore(), each racing the
                        # exact same way against a settle_fn registered
                        # only afterward. ``page.expect_response()`` is
                        # Playwright/Patchright's own built-in fix for
                        # this: a context manager that arms its listener
                        # *before* the code inside its ``with`` block
                        # runs, so the trigger and the wait for its own
                        # response can never race, by construction --
                        # matched here on the real ``/api/feed`` endpoint
                        # templates/feed.html's own loadMore() calls. A
                        # ``TimeoutError`` (no matching response within
                        # ``DEFAULT_PROGRESSIVE_NETWORK_IDLE_TIMEOUT_MS``)
                        # is a real, direct signal that no new request
                        # was ever sent for this scroll step -- feed.html's
                        # own ``hasNext`` has already gone ``false`` --
                        # not a guess, so ``scroll_and_collect`` (_scroll.py)
                        # stops early rather than wasting the remaining
                        # ``max_attempts`` on guaranteed no-ops.
                        # RequestCounter/poll_until_idle (_scroll.py) stay
                        # in that module as independently-tested, still
                        # potentially reusable utilities -- just no
                        # longer wired into this progressive-collection
                        # loop, which no longer needs (or should trust)
                        # a general "is anything happening on the
                        # network" signal instead of the specific
                        # response its own trigger actually caused.
                        progressive_scroll_ended_early = False
                        consecutive_scroll_stalls = 0
                        # docs/REQUIREMENTS.md section 9 entry 17, a
                        # real gap a user review found (not a temporary
                        # investigation artifact -- always on, like
                        # load_more_calls/load_more_dropped, never
                        # gated behind TITAN_DEBUG_LOADING_RACE): the
                        # only verification this whole progressive path
                        # ever had was the *aggregate* final item
                        # count -- mathematically sufficient to catch
                        # any real loss (every post_id is globally
                        # unique per crawl, so a lost item can never
                        # hide behind a duplicate), but gives zero
                        # visibility into *which* page's window was the
                        # one actually lost. Every real ``/api/feed``
                        # response's own ``edges`` already names exactly
                        # which post_ids that page claims to have sent
                        # -- accumulated here as the crawl's own
                        # ground truth, independent of whatever
                        # collect_fn() later manages to read back from
                        # the DOM/HTML.
                        progressive_page_post_ids: list[str] = []
                        # docs/REQUIREMENTS.md section 9 entry 20 (item
                        # 10's mouse-simulation phase): built once per
                        # solve, not per scroll attempt -- an OxyMouse
                        # instance is cheap to build but there's no
                        # reason to rebuild it on every single hover.
                        # `_last_cursor_position` tracks where the
                        # (virtual) cursor actually is between attempts:
                        # Playwright itself exposes no "read back the
                        # current mouse position" API, so this closure
                        # variable is the only way
                        # `_hover_feed_container_before_scroll` below
                        # knows the real "from" point for the next
                        # curved move. Starts at (200, 200) -- the same
                        # fixed point this file's own `hover_fn=None`
                        # fallback (``_scroll.py``'s ``scroll_and_collect``)
                        # has used unmodified since entry 17's "Fourth
                        # revision", already proven safe here. **Not
                        # (0, 0):** confirmed by hand, directly against
                        # this exact target, that ``page.mouse.move(0, 0)``
                        # hangs *indefinitely* (a real Camoufox/headless-
                        # Firefox quirk moving the synthetic cursor to
                        # the literal viewport origin, force-killed after
                        # 30s with no return -- not merely slow) on a
                        # real, non-``about:blank`` page, even though the
                        # identical call returns in milliseconds on
                        # ``about:blank`` -- (0, 0) looked like the
                        # obvious "Playwright's own documented starting
                        # position" choice at first, but was never
                        # actually exercised against a real page before
                        # this was caught, exactly the kind of assumption
                        # this project's own "لا افتراض قيد بيئة" rule
                        # exists to catch. A real, already-proven-safe
                        # value is strictly safer than a theoretically
                        # "more accurate" one that has never actually
                        # been exercised against a real page.
                        _mouse_path_generator = oxymouse_path_generator()
                        _last_cursor_position: tuple[int, int] = (200, 200)
                        # TEMPORARY DIAGNOSTIC, TITAN_DEBUG_LOADING_RACE
                        # -gated (docs/REQUIREMENTS.md section 9 entry 17,
                        # a user review's direct follow-up: *why* does
                        # page.mouse.wheel() sometimes produce zero real
                        # scroll for 11-17s at a time, when it works fine
                        # the rest of the time?). See
                        # _pre_trigger_container_diagnostic's own
                        # docstring below for what each entry means.
                        progressive_container_diagnostic_log: list[str] = []
                        # TEMPORARY DIAGNOSTIC (docs/REQUIREMENTS.md
                        # section 9 entry 17, direct-evidence review of
                        # the "Sixth revision" fix's own remaining
                        # failures -- explicit user request: "مش بس
                        # العدد، إيه اللي حصل تحديدًا"). One entry per
                        # trigger_and_wait_fn call, in order -- answers
                        # with certainty (not inferred from the final
                        # item count alone) whether a given attempt
                        # timed out, or got a real response and exactly
                        # what has_next_page said. TITAN_DEBUG_LOADING_RACE
                        # -gated, same as every other exploratory
                        # diagnostic in this investigation.
                        progressive_scroll_attempt_log: list[str] = []

                        def _read_feed_attr(attr: str) -> str | None:
                            """Reads one of templates/feed.html's own
                            `container` diagnostic attributes
                            (``data-load-more-calls``/``data-load-more-dropped``)
                            -- a DOM attribute, not a ``window.*``
                            property. **Real, CI-confirmed fix
                            (docs/REQUIREMENTS.md section 9 entry 17's
                            `load_more_calls`/`load_more_dropped`
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

                        def _hover_feed_container_before_scroll() -> bool:
                            """docs/REQUIREMENTS.md section 9 entry 17's
                            "Eighth revision" -- a real, CI-confirmed
                            regression the "Seventh revision"'s bare
                            ``page.locator(...).hover()`` introduced (see
                            ``_scroll.py``'s own module docstring for the
                            full reasoning and the real CI evidence,
                            run 33275376646): unlike the blind
                            ``page.mouse.move()`` it replaced, ``hover()``
                            performs a real actionability check -- an
                            unhandled interstitial overlay genuinely
                            intercepting pointer events over the
                            container makes it block for its own default
                            30 real seconds, then raise an exception this
                            module never caught, crashing the *entire*
                            solve.

                            ``force=True`` (skip the actionability check
                            entirely) was considered and rejected -- a
                            cited source ("17 Playwright Testing
                            Mistakes") names it explicitly as an
                            anti-pattern that hides a real problem
                            instead of fixing it, recommending instead:
                            dismiss the actual blocking overlay first,
                            never force past it. This does exactly that,
                            in the order requested:

                            1. Hover with a short, fail-fast
                               ``DEFAULT_PROGRESSIVE_HOVER_TIMEOUT_MS``
                               (3000ms, not this module's own 30000ms
                               default) instead of waiting the full
                               default -- succeeds immediately when
                               nothing is blocking, the common case.
                            2. On a timeout, reuse the *exact* same
                               ``click_selector`` dismiss mechanism
                               entries 9/16 already established (the
                               same real button a real user would click)
                               -- harmless (a no-op the caller already
                               expects to sometimes not apply) when
                               ``click_selector`` isn't configured for
                               this particular target at all.
                            3. Retry the hover once more, same short
                               timeout.

                            Returns ``False`` (never raises) only if the
                            container is *still* blocked after that --
                            an unknown, unhandled kind of overlay this
                            function has no more specific recourse for
                            -- logged clearly (this project's own "No
                            Silent Failure" convention) so
                            ``scroll_and_collect`` can stop the loop
                            gracefully instead of the whole solve
                            crashing.

                            **docs/REQUIREMENTS.md section 9 entry 20
                            (item 10's mouse-simulation phase):** before
                            the actionability-checked ``hover()`` calls
                            below, the (virtual) cursor is first walked
                            along a real, curved path toward the
                            container's own current center --
                            :func:`~src.providers.antibot._mouse_movement.move_mouse_along_path`,
                            reusing this exact hook rather than a new
                            one (see that module's own docstring for the
                            full reasoning: ``Locator.hover()`` itself
                            snaps the cursor there in a single instant
                            jump, confirmed by reading Playwright's own
                            source -- not what a real user's cursor
                            does). This only changes *how* the cursor
                            gets there; it changes nothing about
                            ``hover()``'s own actionability check,
                            timeout, or the dismiss-and-retry recovery
                            below -- a curved approach path landing on
                            an obscured container is exactly as blocked
                            as an instant jump onto one, and ``hover()``
                            still catches that the same way it always
                            has. ``container.bounding_box()`` returning
                            ``None`` (the container itself isn't visible
                            at all, as opposed to merely obscured by an
                            overlay -- confirmed from Playwright's own
                            source: unlike ``hover()``, ``bounding_box()``
                            performs no pointer-interception check of its
                            own) skips the curved movement entirely and
                            falls through to the unchanged ``hover()``
                            calls below, which handle that case exactly
                            as they already did before this revision.
                            """
                            nonlocal _last_cursor_position
                            container = page.locator(_FEED_CONTAINER_SELECTOR)
                            try:
                                box = container.bounding_box(
                                    timeout=DEFAULT_PROGRESSIVE_HOVER_TIMEOUT_MS
                                )
                            except (PlaywrightTimeoutError, PlaywrightError) as exc:
                                box = None
                                logger.debug(
                                    "camoufox_provider.progressive_hover_bounding_box_failed",
                                    extra={"url": url, "reason": str(exc)},
                                )
                            if box is not None:
                                target_x = int(box["x"] + box["width"] / 2)
                                target_y = int(box["y"] + box["height"] / 2)
                                move_mouse_along_path(
                                    page,
                                    *_last_cursor_position,
                                    target_x,
                                    target_y,
                                    _mouse_path_generator,
                                )
                                _last_cursor_position = (target_x, target_y)
                            try:
                                container.hover(timeout=DEFAULT_PROGRESSIVE_HOVER_TIMEOUT_MS)
                                return True
                            except PlaywrightTimeoutError:
                                pass
                            if click_selector:
                                try:
                                    page.locator(click_selector).click(
                                        timeout=DEFAULT_PROGRESSIVE_HOVER_TIMEOUT_MS
                                    )
                                except (PlaywrightTimeoutError, PlaywrightError) as exc:
                                    # Not necessarily a problem -- the
                                    # overlay blocking the hover might not
                                    # even be the one click_selector
                                    # dismisses (a different, unhandled
                                    # kind entirely); logged, not
                                    # swallowed, and the retry below still
                                    # gets a fair attempt regardless.
                                    logger.debug(
                                        "camoufox_provider.progressive_hover_dismiss_click_failed",
                                        extra={"url": url, "reason": str(exc)},
                                    )
                            try:
                                container.hover(timeout=DEFAULT_PROGRESSIVE_HOVER_TIMEOUT_MS)
                                return True
                            except PlaywrightTimeoutError as exc:
                                logger.warning(
                                    "camoufox_provider.progressive_hover_blocked",
                                    extra={"url": url, "reason": str(exc)},
                                )
                                return False

                        def _pre_trigger_container_diagnostic() -> str:
                            """docs/REQUIREMENTS.md section 9 entry 17,
                            a user review's direct follow-up question:
                            is ``page.mouse.wheel()``'s intermittent
                            multi-second silence actually a *container*
                            problem -- detached from the DOM, moved out
                            from under the fixed ``(200, 200)`` cursor
                            position ``scroll_and_collect``'s own
                            one-time ``page.mouse.move()`` leaves it at
                            for the *entire* crawl (never re-issued per
                            step), or momentarily replaced by something
                            else during a re-render -- rather than
                            guessed. Deliberately reads, never calls
                            ``.hover()``: a real ``.hover()`` call moves
                            the actual (virtual) cursor, which would
                            itself change the very thing under test;
                            ``document.elementFromPoint(200, 200)``
                            answers the identical "is the feed container
                            actually what's under the fixed cursor
                            position right now" question with zero side
                            effect. Called once per scroll attempt,
                            immediately *before* that attempt's own
                            trigger -- so a run of consecutive timeouts
                            can be read back against what the container's
                            own state looked like at the start of each
                            one, not just at the end.

                            Never allowed to fail the actual crawl --
                            any exception here (a closed page, a
                            mid-navigation frame) is caught and encoded
                            into the string itself instead of raised.
                            """
                            try:
                                container = page.locator(_FEED_CONTAINER_SELECTOR).first
                                count = container.count()
                                box = container.bounding_box() if count > 0 else None
                                element_at_cursor = page.evaluate(
                                    "(() => { const el = document.elementFromPoint(200, 200);"
                                    " return el ? (el.getAttribute('data-role') || el.tagName)"
                                    " : null; })()"
                                )
                                elapsed_ms = page.evaluate("performance.now()")
                                return (
                                    f"count={count}:box={box}:at_cursor={element_at_cursor}:"
                                    f"t={elapsed_ms:.0f}"
                                )
                            except (PlaywrightError, PlaywrightTimeoutError) as exc:
                                return f"diagnostic_failed:{exc}"

                        def _trigger_and_wait_for_feed_response(
                            trigger_fn: Callable[[], None],
                        ) -> bool:
                            """The actual "trigger-and-wait" pattern
                            (this block's own comment above has the
                            full reasoning): arms the response listener
                            *before* calling ``trigger_fn`` (the real
                            ``page.mouse.wheel()`` scroll -- built and
                            passed in by ``_scroll.py``'s
                            ``scroll_and_collect``, which owns the
                            engine-agnostic scroll mechanics and stays
                            unaware of this URL-matching, target-specific
                            wait).

                            **Sixth revision (docs/REQUIREMENTS.md
                            section 9 entry 17, a real, CI-confirmed
                            correction to the "Fifth revision" above --
                            not erased, appended):** stopping
                            unconditionally on the *first* timeout was
                            itself wrong, confirmed by real local
                            evidence -- ``page.mouse.wheel()`` sometimes
                            produces no scroll event at all for reasons
                            unrelated to how much real content is left,
                            so a lone timeout is not proof pagination
                            has ended. Two real signals now, preferred
                            in this order:

                            1. The actual ``/api/feed`` response body's
                               own ``page_info.has_next_page`` --
                               ``templates/feed.html``'s own
                               authoritative source of truth for
                               whether more pages exist, not an
                               inference from timing at all. ``False``
                               here means real, confirmed end of
                               pagination -- stop immediately.
                            2. Only when no response arrives to read
                               that field from at all (a genuine
                               timeout): tolerate up to
                               ``DEFAULT_PROGRESSIVE_MAX_CONSECUTIVE_SCROLL_STALLS``
                               *consecutive* ones (a real, believable
                               "this specific wheel() attempt just
                               didn't land" retry budget) before giving
                               up -- never on the very first one alone.
                            """
                            nonlocal progressive_scroll_ended_early, consecutive_scroll_stalls
                            if debug_loading_race:
                                pre_trigger_diagnostic = _pre_trigger_container_diagnostic()
                            try:
                                with page.expect_response(
                                    lambda response: "/api/feed" in response.url
                                    and response.status == 200,
                                    timeout=DEFAULT_PROGRESSIVE_NETWORK_IDLE_TIMEOUT_MS,
                                ) as response_info:
                                    trigger_fn()
                                consecutive_scroll_stalls = 0
                                # `None` (not just False) whenever the body
                                # isn't real JSON, or lacks this field --
                                # e.g. a page that isn't this one at all --
                                # matching this file's own established
                                # "harmless on any other page" convention.
                                # Never allowed to fail the actual crawl.
                                has_next_page = None
                                page_post_ids: list[str] = []
                                try:
                                    body = response_info.value.json()
                                    has_next_page = body.get("page_info", {}).get(
                                        "has_next_page"
                                    )
                                    # docs/REQUIREMENTS.md section 9 entry 17,
                                    # answering a user review's Q3: the raw
                                    # ground truth for exactly which
                                    # post_ids this one page's response
                                    # claims to have sent -- deliberately
                                    # *not* diffed against anything here (a
                                    # naive "collected this step == this
                                    # page's own edges count" check would be
                                    # wrong-by-design: this same file's own
                                    # test, test_mock_target_dom_
                                    # virtualization_progressive_live.py,
                                    # already derived and confirmed that
                                    # only the *last* window_size of each
                                    # page's edges ever survive eviction
                                    # long enough for any read to catch --
                                    # the rest are correctly gone before
                                    # collect_fn() next runs, not lost by a
                                    # bug). What "should" survive depends on
                                    # test-target-specific trim/window_size
                                    # math this generic provider has no
                                    # business knowing -- so this stays raw
                                    # evidence, aggregated below into a
                                    # count only, for whichever caller (a
                                    # test, a human reading this log line)
                                    # does have that domain knowledge.
                                    page_post_ids = [
                                        edge["post"]["id"]
                                        for edge in body.get("edges", [])
                                        if isinstance(edge, dict)
                                        and isinstance(edge.get("post"), dict)
                                        and "id" in edge["post"]
                                    ]
                                except (ValueError, AttributeError, TypeError, KeyError) as exc:
                                    # Not this page's own JSON shape at all
                                    # (invalid/absent JSON body, or a body
                                    # that parsed but isn't the expected
                                    # dict-of-a-dict) -- logged, not
                                    # silently swallowed, but genuinely
                                    # harmless: `has_next_page` staying
                                    # `None` just means the caller falls
                                    # through to the consecutive-stalls
                                    # fallback below instead, same as any
                                    # page that isn't this one.
                                    logger.debug(
                                        "camoufox_provider.progressive_response_not_feed_json",
                                        extra={"url": url, "reason": str(exc)},
                                    )
                                progressive_page_post_ids.extend(page_post_ids)
                                if debug_loading_race:
                                    progressive_scroll_attempt_log.append(
                                        f"success:has_next={has_next_page}:edges={len(page_post_ids)}"
                                    )
                                    progressive_container_diagnostic_log.append(
                                        f"pre={pre_trigger_diagnostic}:outcome=success"
                                    )
                                if has_next_page is False:
                                    progressive_scroll_ended_early = True
                                    return False
                                return True
                            except PlaywrightTimeoutError:
                                consecutive_scroll_stalls += 1
                                if debug_loading_race:
                                    progressive_container_diagnostic_log.append(
                                        f"pre={pre_trigger_diagnostic}:outcome=timeout"
                                    )
                                    progressive_scroll_attempt_log.append(
                                        f"timeout:consecutive={consecutive_scroll_stalls}"
                                    )
                                if (
                                    consecutive_scroll_stalls
                                    >= DEFAULT_PROGRESSIVE_MAX_CONSECUTIVE_SCROLL_STALLS
                                ):
                                    progressive_scroll_ended_early = True
                                    return False
                                return True

                        if extraction_selectors is not None:
                            items = collect_live_dom_items_progressively(
                                page,
                                extraction_selectors.item,
                                extraction_selectors.fields,
                                DEFAULT_PROGRESSIVE_MAX_SCROLL_ATTEMPTS,
                                DEFAULT_PROGRESSIVE_SCROLL_PAUSE_MS,
                                trigger_and_wait_fn=_trigger_and_wait_for_feed_response,
                                hover_fn=_hover_feed_container_before_scroll,
                            )
                        else:
                            html_snapshots = collect_html_snapshots(
                                page,
                                DEFAULT_PROGRESSIVE_MAX_SCROLL_ATTEMPTS,
                                DEFAULT_PROGRESSIVE_SCROLL_PAUSE_MS,
                                trigger_and_wait_fn=_trigger_and_wait_for_feed_response,
                                hover_fn=_hover_feed_container_before_scroll,
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
                    # a call" -- still real, useful evidence regardless
                    # of which scroll/wait mechanism drives collection.
                    # Harmless (reads back 0/0) on any page that isn't
                    # this one.
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
                    # docs/REQUIREMENTS.md section 9 entry 17, answering a
                    # user review's Q3 (see progressive_page_post_ids's own
                    # definition above for why this is a count, not a
                    # "missing ids" diff): the API's own authoritative
                    # tally of every post_id it ever confirmed sending
                    # across every successful trigger-and-wait call this
                    # solve made. `_unique` catches a genuinely different
                    # class of bug than anything else logged here -- the
                    # API itself resending an id it already sent on an
                    # earlier page (a content_generator.py/mock-target bug,
                    # not a scraper bug, were it ever to happen; content_
                    # generator.py's own `{seed}-post-{index}` scheme is
                    # global-index-keyed specifically to rule this out).
                    progressive_api_reported_post_id_count = (
                        len(progressive_page_post_ids) if progressive_extraction else None
                    )
                    progressive_api_reported_post_id_count_unique = (
                        len(set(progressive_page_post_ids)) if progressive_extraction else None
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
                            # Virtualization Instability" investigation,
                            # "Fifth revision": whether scroll_and_collect
                            # stopped before exhausting
                            # DEFAULT_PROGRESSIVE_MAX_SCROLL_ATTEMPTS
                            # because a trigger-and-wait call timed out
                            # (no matching /api/feed response -- a real
                            # signal pagination had already ended, not a
                            # guess). `None` when progressive_extraction
                            # is False (the mechanism never runs at all).
                            "progressive_scroll_ended_early": (
                                progressive_scroll_ended_early if progressive_extraction else None
                            ),
                            # TEMPORARY DIAGNOSTIC, TITAN_DEBUG_LOADING_RACE
                            # -gated (see this block's own definition
                            # above): the exact outcome of every single
                            # trigger_and_wait_fn call, in order -- direct
                            # evidence for which of "has_next_page came
                            # back false early", "genuine repeated
                            # timeouts", or neither (calls=5 but still
                            # short -- a different mechanism entirely)
                            # actually happened, instead of inferring it
                            # from the final item count alone.
                            "progressive_scroll_attempt_log": (
                                progressive_scroll_attempt_log
                                if progressive_extraction and debug_loading_race
                                else None
                            ),
                            # TEMPORARY DIAGNOSTIC, TITAN_DEBUG_LOADING_RACE
                            # -gated: the feed container's own attachment
                            # state, bounding box, and whatever DOM
                            # element is actually under the fixed
                            # (200, 200) cursor position, read right
                            # before *every* scroll attempt -- see
                            # _pre_trigger_container_diagnostic's own
                            # docstring above for the full reasoning.
                            "progressive_container_diagnostic_log": (
                                progressive_container_diagnostic_log
                                if progressive_extraction and debug_loading_race
                                else None
                            ),
                            "load_more_calls": load_more_calls,
                            "load_more_dropped": load_more_dropped,
                            "progressive_api_reported_post_id_count": (
                                progressive_api_reported_post_id_count
                            ),
                            "progressive_api_reported_post_id_count_unique": (
                                progressive_api_reported_post_id_count_unique
                            ),
                            "apparmor_denials_during_solve": apparmor_denials_during_solve,
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
                    if use_accumulated_profile:
                        # docs/REQUIREMENTS.md section 9 entry 21, Step
                        # 2: only on a genuinely successful solve --
                        # deliberately not attempted in any exception
                        # path (a half-failed solve's own partial state
                        # isn't worth the added complexity of persisting
                        # it too). Never allowed to fail the actual
                        # crawl over a jar-write problem (a full disk, a
                        # permissions issue) -- logged, not propagated:
                        # losing the accumulated profile for next time is
                        # a real degradation, not a reason an otherwise-
                        # successful solve should raise.
                        try:
                            # Playwright's own StorageState is a real,
                            # concrete TypedDict, structurally
                            # compatible with cookie_jar_manager.py's own
                            # plain dict[str, Any] contract (deliberately
                            # loosely typed there -- see that module's
                            # own module docstring for why it stays
                            # engine-agnostic) -- mypy just doesn't
                            # recognize a TypedDict as a `dict[str,
                            # Any]` subtype automatically.
                            record_new_session(
                                cookie_jar_path,
                                context.storage_state(),  # type: ignore[arg-type]
                            )
                        except OSError as exc:
                            logger.warning(
                                "camoufox_provider.cookie_jar_save_failed",
                                extra={
                                    "url": url,
                                    "cookie_jar_path": cookie_jar_path,
                                    "reason": str(exc),
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
            extra={
                "url": url,
                "apparmor_denials_during_solve": apparmor_denials_during_solve,
                "browser_crashed": browser_crashed,
            },
        )
        # docs/REQUIREMENTS.md section 9 entry 17: BrowserCrashedError
        # only when page.on("crash")/browser.on("disconnected") actually
        # fired -- a real, classified engine crash, not merely "some
        # PlaywrightError happened" (a denied request or a legitimate
        # timeout raises PlaywrightError too, and must not be retried the
        # same way -- CamoufoxProvider.solve()'s own retry loop below
        # only catches this specific subclass). See
        # _classify_solve_exception's own docstring for why this
        # decision is a separate, directly-unit-tested pure function.
        raise _classify_solve_exception(browser_crashed, url, exc) from exc


class CamoufoxProvider(AntibotProvider):
    """Solves anti-bot challenges by driving a real Camoufox browser directly."""

    def __init__(
        self,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        post_load_wait_ms: int = DEFAULT_POST_LOAD_WAIT_MS,
        solve_fn: CamoufoxSolveFn | None = None,
        logger: Logger | None = None,
        max_browser_crash_attempts: int = DEFAULT_MAX_BROWSER_CRASH_ATTEMPTS,
        cookie_jar_path: str = DEFAULT_COOKIE_JAR_PATH,
    ) -> None:
        if timeout_ms <= 0:
            raise AntibotError(f"timeout_ms must be > 0, got {timeout_ms}")
        if post_load_wait_ms < 0:
            raise AntibotError(f"post_load_wait_ms must be >= 0, got {post_load_wait_ms}")
        if max_browser_crash_attempts <= 0:
            raise AntibotError(
                f"max_browser_crash_attempts must be > 0, got {max_browser_crash_attempts}"
            )
        self._timeout_ms = timeout_ms
        self._post_load_wait_ms = post_load_wait_ms
        self._solve_fn = solve_fn or _default_camoufox_solve
        self.logger = logger or get_logger(__name__)
        self._max_browser_crash_attempts = max_browser_crash_attempts
        # docs/REQUIREMENTS.md section 9 entry 21, Step 2: one path per
        # provider *instance*, not per solve() call -- see
        # cookie_jar_manager.py's own module docstring for why this is
        # deliberately one project-wide default in practice
        # (DEFAULT_COOKIE_JAR_PATH), configurable here mainly for tests
        # that need real isolation (a tmp_path of their own).
        self._cookie_jar_path = cookie_jar_path

    def solve(
        self,
        url: str,
        click_selector: str | None = None,
        extraction_selectors: LiveDomSelectors | None = None,
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
        warm_session_urls: list[str] | None = None,
        use_accumulated_profile: bool = False,
    ) -> Solution:
        # docs/REQUIREMENTS.md section 9 entry 17: a real, kernel-log-
        # confirmed Firefox engine crash (BrowserCrashedError's own
        # docstring) is retried, on a fresh browser instance each time
        # (self._solve_fn's own `with Camoufox(...)` already creates a
        # brand-new one on every call -- nothing carries over) -- bounded
        # by max_browser_crash_attempts, never open-ended. Any *other*
        # AntibotError (a denied request, a legitimate timeout, no items
        # found) is not retried at all -- retrying those would either
        # waste time on a failure retrying can't fix, or silently mask a
        # real, reproducible problem behind an eventual lucky pass.
        for attempt in range(1, self._max_browser_crash_attempts + 1):
            try:
                raw = self._solve_fn(
                    url,
                    self._timeout_ms,
                    self._post_load_wait_ms,
                    click_selector,
                    extraction_selectors,
                    progressive_extraction,
                    login_flow,
                    warm_session_urls,
                    use_accumulated_profile,
                    self._cookie_jar_path,
                )
            except BrowserCrashedError as exc:
                if attempt >= self._max_browser_crash_attempts:
                    self.logger.error(
                        "camoufox_provider.solve_failed",
                        extra={
                            "url": url,
                            "browser_crash_attempts_exhausted": attempt,
                        },
                    )
                    raise
                self.logger.warning(
                    "camoufox_provider.browser_crash_retry",
                    extra={"url": url, "attempt": attempt, "reason": str(exc)},
                )
                continue
            except AntibotError:
                self.logger.error("camoufox_provider.solve_failed", extra={"url": url})
                raise
            break

        return Solution(
            url=raw.url,
            html=raw.html,
            status_code=raw.status,
            cookies=raw.cookies,
            items=raw.items,
            html_snapshots=raw.html_snapshots,
            solved_at=datetime.now(tz=UTC),
        )
