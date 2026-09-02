"""Patchright implementation of the :class:`AntibotProvider` interface.

Patchright (https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python,
Apache-2.0) is a real, drop-in replacement for Playwright's own Python API
(``from patchright.sync_api import sync_playwright`` -- same
``p.chromium.launch()`` / ``browser.new_page()`` / ``page.goto()`` shape as
plain Playwright, confirmed against its own PyPI metadata and README before
writing this) that patches Chromium's own automation fingerprints out at
the driver level. It is Chromium-only (no Firefox/Webkit) and needs its
own separate patched-Chromium binary (``patchright install chromium`` --
does *not* reuse a plain-Playwright-installed Chromium).

This is the "lighter than Camoufox" option
(:class:`~src.providers.antibot.camoufox_provider.CamoufoxProvider`'s own
module docstring forward-references this module): built on the exact same
idea -- drive the browser in-process and hold it open past the page's
``load`` event so an async, post-load challenge (e.g. Anubis's real
proof-of-work flow) gets a real chance to finish, the one thing
:class:`~src.providers.antibot.byparr_provider.ByparrProvider`'s external
HTTP API structurally cannot do -- but reusing the Playwright-shaped
Chromium automation this project already depends on
(``playwright_middleware.py``) plus Patchright as a stealth layer on top of
it, instead of a whole separate Firefox-based stealth browser. Whether
"lighter" also means "weaker against this stack's real Anubis challenge"
is not assumed here -- see docs/REQUIREMENTS.md's "Antibot Provider
Comparison" table for the actual, CI-confirmed result once this provider
has been run against it for real.

The actual browser-driving call is injectable (``solve_fn``) so unit tests
never launch a real browser or touch the network.

**Scroll capability (docs/REQUIREMENTS.md section 9 entry 13):** same
gap and same fix as
:class:`~src.providers.antibot.camoufox_provider.CamoufoxProvider`'s own
module docstring documents -- see that for the full explanation of why
``PlaywrightMiddleware``'s existing scroll loop was structurally
unreachable for any Anubis-protected target.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from logging import Logger
from typing import Any, NamedTuple

from src.core.exceptions import AntibotError, BrowserCrashedError
from src.core.interfaces.antibot_provider import (
    AntibotProvider,
    LiveDomSelectors,
    LoginFlow,
    Solution,
)
from src.diagnostics.failure_registry import record_failure
from src.diagnostics.failure_taxonomy import FailureCategory, FailureRecord
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
from src.providers.antibot._tracing import build_trace_path, trace_dir_from_env
from src.providers.antibot.cookie_jar_manager import load_accumulated_state, record_new_session

DEFAULT_TIMEOUT_MS = 30_000
# Same value and same reasoning as CamoufoxProvider's identical constant
# (docs/REQUIREMENTS.md section 9 entry 17) -- see camoufox_provider.py's
# own comment for the full explanation.
DEFAULT_MAX_BROWSER_CRASH_ATTEMPTS = 3
# Same reasoning and same default as CamoufoxProvider's
# DEFAULT_POST_LOAD_WAIT_MS (camoufox_provider.py) -- long enough for
# Anubis's own real, difficulty-2-by-default proof-of-work challenge to
# compute and round-trip.
DEFAULT_POST_LOAD_WAIT_MS = 5_000
# Same values and same reasoning as CamoufoxProvider's identical
# constants (docs/REQUIREMENTS.md section 9 entry 13).
DEFAULT_MAX_SCROLL_ATTEMPTS = 8
DEFAULT_SCROLL_PAUSE_MS = 700
# Same values and same reasoning (a real, CI-confirmed async-race
# shortfall, not a guess) as CamoufoxProvider's identical constants --
# see camoufox_provider.py's own comment for the full explanation.
DEFAULT_PROGRESSIVE_MAX_SCROLL_ATTEMPTS = 10
DEFAULT_PROGRESSIVE_SCROLL_PAUSE_MS = 1_500
# Same value and same reasoning as CamoufoxProvider's identical constant
# (docs/REQUIREMENTS.md section 9's "DOM Virtualization Instability"
# investigation) -- see camoufox_provider.py's own comment for the full
# explanation.
DEFAULT_PROGRESSIVE_NETWORK_IDLE_TIMEOUT_MS = 5_000
# Same value and same reasoning as CamoufoxProvider's identical constant
# (docs/REQUIREMENTS.md section 9 entry 17's "Sixth revision") -- see
# camoufox_provider.py's own comment for the full explanation.
DEFAULT_PROGRESSIVE_MAX_CONSECUTIVE_SCROLL_STALLS = 3
# Same value and same reasoning as CamoufoxProvider's identical constant
# (docs/REQUIREMENTS.md section 9 entry 17's "Seventh revision") -- see
# camoufox_provider.py's own comment for the full explanation.
_FEED_CONTAINER_SELECTOR = '[data-role="feed"]'
# Same value and same reasoning as CamoufoxProvider's identical constant
# (docs/REQUIREMENTS.md section 9 entry 17's "Eighth revision") -- see
# camoufox_provider.py's own comment for the full explanation.
DEFAULT_PROGRESSIVE_HOVER_TIMEOUT_MS = 3_000
# Same value and same reasoning as CamoufoxProvider's identical constant
# (docs/REQUIREMENTS.md section 9 entry 21, Step 2) -- see
# camoufox_provider.py's own comment for the full explanation: one
# shared, project-wide default rather than per-target.
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
# use_accumulated_profile, cookie_jar_path, user_agent_override) -> raw
# browser result
PatchrightSolveFn = Callable[
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
        "str | None",
    ],
    _RawSolve,
]


# Same reasoning as camoufox_provider.py's identical pragma (docs/
# REQUIREMENTS.md section 9's "DOM Virtualization Instability"
# investigation): this whole function's body needs a real, live browser
# and is exercised only via tests/integration -- see that module's own
# comment for the full explanation. PatchrightProvider.solve() itself
# (unit-tested via the injectable solve_fn) is unaffected.
def _classify_solve_exception(
    browser_crashed: bool, url: str, exc: Exception
) -> AntibotError:
    """docs/REQUIREMENTS.md section 9 entry 17: same pure,
    independently-unit-tested crash-classification decision as
    camoufox_provider.py's identical function -- see its own docstring
    for the full reasoning."""
    if browser_crashed:
        return BrowserCrashedError(
            f"patchright's browser engine crashed mid-solve for {url}: {exc}"
        )
    return AntibotError(f"patchright failed to solve {url}: {exc}")


def _default_patchright_solve(  # pragma: no cover
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
    user_agent_override: str | None = None,
) -> _RawSolve:
    """Drive a real Patchright-stealthed Chromium: navigate, (optionally)
    click, wait past ``load``, read, close.

    ``warm_session_urls``/``use_accumulated_profile``/``cookie_jar_path``
    (docs/REQUIREMENTS.md section 9 entry 21, Step 2): same real
    mechanics and reasoning as
    :func:`~src.providers.antibot.camoufox_provider._default_camoufox_solve`'s
    identical parameters -- see that function's own docstring for the
    full explanation.

    ``click_selector``: same reasoning as
    :func:`~src.providers.antibot.camoufox_provider._default_camoufox_solve`'s
    identical parameter -- this provider drives a real Playwright-shaped
    ``Page`` too, so it can genuinely click. ``post_load_wait_ms`` (already
    configurable) is reused as the "wait after click" delay, not a new
    parameter.

    Same JSON handling as ``_default_camoufox_solve`` -- including its
    second-round fix: reads the raw network body of the *last*
    main-frame navigation response (tracked via
    ``page.on("response", ...)``), not just whatever ``page.goto()``
    itself returned, since an Anubis-protected URL's real target is
    only reached via an async, client-side redirect *after* that first
    response (docs/REQUIREMENTS.md section 9 entry 9's second round).
    Chromium (which this provider drives) has its own built-in JSON
    viewer with the identical DOM-wrapping risk confirmed for real
    against Camoufox's Firefox -- not independently confirmed for
    Patchright/Chromium specifically (it never reaches a JSON endpoint
    in this stack at all, entry 7's Anubis deny), applied here on the
    same principle rather than left inconsistent between the two
    real-browser providers.

    ``extraction_selectors``: same reasoning and same
    :func:`~src.providers.antibot._live_dom.extract_live_dom_items` call
    as ``_default_camoufox_solve``'s identical parameter
    (docs/REQUIREMENTS.md section 9 entry 12) -- extracted from the live
    ``page`` before it closes below, since a shadow root is never
    included in ``page.content()``'s serialized string regardless of
    which browser (Chromium here, Firefox there) produced it.

    ``progressive_extraction``: same reasoning, order, and merge-by-
    ``post_id`` behavior as
    :func:`~src.providers.antibot.camoufox_provider._default_camoufox_solve`'s
    identical parameter (docs/REQUIREMENTS.md section 9 entry 14) -- see
    that function's own docstring for the full explanation, including
    the network-idle ``settle_fn`` wait (docs/REQUIREMENTS.md section 9's
    "DOM Virtualization Instability" investigation) each scroll step now
    does before collecting.

    ``login_flow``: same reasoning, order, and success/failure decision
    (real-response-status-driven, not assumed) as
    :func:`~src.providers.antibot.camoufox_provider._default_camoufox_solve`'s
    identical parameter (docs/REQUIREMENTS.md section 9 entry 15) -- see
    that function's own docstring for the full explanation.

    ``user_agent_override`` (docs/REQUIREMENTS.md section 9 entry 24/27): same mechanism as
    :func:`~src.providers.antibot.camoufox_provider._default_camoufox_solve`'s
    identical parameter -- passed straight through to
    ``browser.new_context(user_agent=...)``. This module never has
    Camoufox's own dual-mode (persistent-vs-multi-context) launch
    ambiguity (this function's own comment on ``browser.new_context()``
    being unconditionally the real thing to call here already explains
    why), so there's no second, unsupported-launch-mode branch to log a
    warning for the way that module's identical parameter needs.

    Raises:
        AntibotError: if the browser fails to launch, navigate, or read
            the page -- wraps Patchright's own launch/navigation/page
            errors (``patchright.sync_api.Error``, the same exception type
            plain Playwright raises, since Patchright is a drop-in
            replacement for it).
    """
    from patchright.sync_api import Error as PatchrightError
    from patchright.sync_api import Response as PatchrightResponse
    from patchright.sync_api import TimeoutError as PatchrightTimeoutError
    from patchright.sync_api import sync_playwright

    logger = get_logger(__name__)
    # docs/REQUIREMENTS.md section 9 entry 17's monitoring-infrastructure
    # investment: off by default (None), active only when TITAN_TRACE_DIR
    # is set -- see _tracing.py's own module docstring for the full
    # reasoning. Same shape as camoufox_provider.py's identical use.
    trace_dir = trace_dir_from_env()
    with sync_playwright() as p:
        try:
            # --disable-dev-shm-usage (docs/REQUIREMENTS.md section 9,
            # JA4 experiment crash investigation): a real, well-documented
            # Chromium mitigation for the exact "Target page, context or
            # browser has been closed" crash this session has observed
            # repeatedly -- redirects Chromium's shared-memory usage to
            # /tmp instead of /dev/shm, avoiding an OOM-kill of the
            # renderer process when shm runs out. Confirmed harmless for
            # patchright's own stealth properties (it only changes where
            # temp shared-memory files live, not any navigator/WebGL/CDP
            # -detectable signal). Added defensively even though every
            # crash actually observed in this investigation was in
            # CamoufoxProvider (Firefox-based), never here -- this flag
            # is Chromium-specific and has no Firefox equivalent, so it
            # cannot fix what was actually crashing; see docs/REQUIREMENTS.md
            # entry 17 for the full, honest writeup of what this
            # investigation did and did not resolve.
            browser = p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        except PatchrightError as exc:
            raise AntibotError(
                f"patchright failed to launch chromium for {url} "
                "(run: patchright install chromium)"
            ) from exc

        # docs/REQUIREMENTS.md section 9 entry 17: same real, kernel-log-
        # confirmed browser-engine-crash mitigation as
        # camoufox_provider.py's identical wiring -- p.chromium.launch()
        # here always returns a genuine (non-persistent-context) Browser,
        # so "disconnected" applies directly, no isinstance check needed
        # the way Camoufox's own dual-mode context manager requires.
        browser_crashed = False

        def _mark_browser_crashed(*_args: object) -> None:
            nonlocal browser_crashed
            browser_crashed = True

        browser.on("disconnected", _mark_browser_crashed)
        try:
            # ignore_https_errors=True (docs/REQUIREMENTS.md section 9,
            # JA4/TLS experiment -- ported from claude/ja4-experiment onto
            # this branch): same justification as camoufox_provider.py's
            # own identical change -- unconditional, a genuine no-op for
            # every existing plain-http:// target (no TLS handshake
            # happens there at all), only takes effect against the
            # JA4-proxy target's self-signed cert.
            #
            # docs/REQUIREMENTS.md section 9 entry 21, Step 2: same
            # `storage_state`-at-context-creation-time mechanism as
            # camoufox_provider.py's identical change -- see that
            # module's own comment for the full reasoning (confirmed
            # directly, both against Playwright's own docs and by hand).
            # `browser.new_page()` here always creates a fresh context
            # implicitly (`p.chromium.launch()` above never returns a
            # persistent context -- this module's own comment on
            # `browser.on("disconnected", ...)` above already confirms
            # that), so there's no isinstance branch needed the way
            # Camoufox's own dual-mode context manager requires --
            # `browser.new_context()` is unconditionally the real thing
            # to call here.
            loaded_state = (
                load_accumulated_state(cookie_jar_path) if use_accumulated_profile else None
            )
            context = browser.new_context(
                ignore_https_errors=True,
                storage_state=loaded_state,  # type: ignore[arg-type]
                user_agent=user_agent_override,
            )
            page = context.new_page()
            page.on("crash", _mark_browser_crashed)
            if trace_dir is not None:
                page.context.tracing.start(screenshots=True, snapshots=True, sources=True)
            # Same reasoning as camoufox_provider.py's identical variable
            # (docs/REQUIREMENTS.md section 9 entry 21, Step 2).
            last_warm_session_url: str | None = None
            if warm_session_urls:
                # docs/REQUIREMENTS.md section 9 entry 21, Step 2: same
                # reasoning as camoufox_provider.py's identical block --
                # every one of these navigations shares this exact
                # page/context, so any cookie a warm-up page sets is
                # already in this browser's real cookie jar by the time
                # the actual login_flow/url navigation below happens.
                # `referer=` chained explicitly for the same reason
                # camoufox_provider.py's identical block documents:
                # page.goto() never derives it automatically.
                for warm_url in warm_session_urls:
                    page.goto(warm_url, timeout=timeout_ms, referer=last_warm_session_url)
                    page.wait_for_timeout(post_load_wait_ms)
                    last_warm_session_url = warm_url
            try:
                try:
                    # Tracks the *last* main-frame navigation response --
                    # see this function's own docstring for why the first
                    # response alone is unreliable for an
                    # Anubis-protected URL. `is_navigation_request()` is
                    # required, not just `resp.frame is page.main_frame`
                    # -- see camoufox_provider.py's identical comment for
                    # the real, confirmed reason (an ordinary fetch/XHR a
                    # page's own JS makes -- e.g. Anubis's own
                    # pass-challenge call -- shares the main frame but is
                    # not a navigation, and was overwriting the real
                    # page's response).
                    last_main_frame_response: PatchrightResponse | None = None

                    def _track_main_frame_response(resp: PatchrightResponse) -> None:
                        nonlocal last_main_frame_response
                        if resp.frame is page.main_frame and resp.request.is_navigation_request():
                            last_main_frame_response = resp

                    page.on("response", _track_main_frame_response)
                    # entry 15: login runs first -- see this function's
                    # own docstring / camoufox_provider.py's identical
                    # block for the full success/failure decision.
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
                            "patchright_provider",
                            login_flow.login_url,
                            url,
                            login_ok,
                            final_status,
                        )
                        initial_response = last_main_frame_response
                    else:
                        # docs/REQUIREMENTS.md section 9 entry 21, Step
                        # 2: same reasoning as camoufox_provider.py's
                        # identical call.
                        initial_response = page.goto(
                            url, timeout=timeout_ms, referer=last_warm_session_url
                        )
                    if click_selector:
                        page.click(click_selector, timeout=timeout_ms)
                    # The same capability CamoufoxProvider's own
                    # solve function relies on: hold the browser open
                    # past `load` so async, post-load challenge JS gets a
                    # real chance to finish, instead of tearing the
                    # browser down the instant `load` fires
                    # (ByparrProvider's own structural constraint) -- or,
                    # if a click just happened above, give whatever it
                    # triggered time to settle before reading content.
                    page.wait_for_timeout(post_load_wait_ms)
                    # Same reasoning and order as CamoufoxProvider's
                    # identical comment (docs/REQUIREMENTS.md section 9
                    # entry 13): scrolled after the wait above, once real
                    # content has actually had a chance to arrive.
                    #
                    # entry 14: same progressive_extraction branching as
                    # camoufox_provider.py's identical block.
                    items: list[dict[str, Any]] | None = None
                    html_snapshots: list[str] | None = None
                    progressive_scroll_ended_early: bool | None = None
                    if progressive_extraction:
                        # docs/REQUIREMENTS.md section 9's "DOM
                        # Virtualization Instability" investigation,
                        # "Fifth revision" -- same real, CI-confirmed
                        # correction as camoufox_provider.py's identical
                        # block: registering a wait *after* the scroll
                        # trigger (this file's own previous
                        # RequestCounter/poll_until_idle-based settle_fn,
                        # like camoufox_provider.py's) is itself a race
                        # whenever the response is fast -- confirmed for
                        # real that even a single, genuine
                        # page.mouse.wheel() call can produce more than
                        # one native scroll-triggered loadMore(), each
                        # capable of racing a settle_fn armed only
                        # afterward. page.expect_response() fixes this by
                        # construction (arms its listener *before* the
                        # code inside its `with` block runs) -- see
                        # camoufox_provider.py's own identical helper for
                        # the full reasoning; this is the same pattern,
                        # just built around Patchright's own
                        # TimeoutError.
                        consecutive_scroll_stalls = 0
                        # docs/REQUIREMENTS.md section 9 entry 17, same
                        # user-review-driven addition as
                        # camoufox_provider.py's identical variable: the
                        # API's own ground truth of every post_id it ever
                        # confirmed sending, across every successful
                        # trigger-and-wait call -- see camoufox_provider.py's
                        # own comment for the full reasoning on why this
                        # stays a raw count, not a "missing ids" diff.
                        progressive_page_post_ids: list[str] = []
                        # docs/REQUIREMENTS.md section 9 entry 20: same
                        # reasoning and shape as camoufox_provider.py's
                        # identical variables, including the (200, 200)
                        # starting value -- see that module's own comment
                        # for the full explanation of why (0, 0) hangs
                        # there. That specific hang was only confirmed
                        # against Camoufox/Firefox, not against this
                        # provider's own Chromium -- (200, 200) is used
                        # here too anyway, both for consistency and
                        # because there is no actual reason to prefer
                        # (0, 0) even if this engine turns out not to
                        # share the bug.
                        _mouse_path_generator = oxymouse_path_generator()
                        _last_cursor_position: tuple[int, int] = (200, 200)

                        def _hover_feed_container_before_scroll() -> bool:
                            """Same real, CI-confirmed regression and
                            fix as camoufox_provider.py's identical
                            helper (docs/REQUIREMENTS.md section 9 entry
                            17's "Eighth revision") -- built around
                            Patchright's own TimeoutError/Error instead.
                            See camoufox_provider.py's own comment for
                            the full reasoning.

                            docs/REQUIREMENTS.md section 9 entry 20:
                            same curved-approach-before-hover upgrade as
                            camoufox_provider.py's identical helper --
                            see that module's own docstring for the full
                            reasoning.
                            """
                            nonlocal _last_cursor_position
                            container = page.locator(_FEED_CONTAINER_SELECTOR)
                            try:
                                box = container.bounding_box(
                                    timeout=DEFAULT_PROGRESSIVE_HOVER_TIMEOUT_MS
                                )
                            except (PatchrightTimeoutError, PatchrightError) as exc:
                                box = None
                                logger.debug(
                                    "patchright_provider.progressive_hover_bounding_box_failed",
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
                            except PatchrightTimeoutError:
                                pass
                            if click_selector:
                                try:
                                    page.locator(click_selector).click(
                                        timeout=DEFAULT_PROGRESSIVE_HOVER_TIMEOUT_MS
                                    )
                                except (PatchrightTimeoutError, PatchrightError) as exc:
                                    logger.debug(
                                        "patchright_provider.progressive_hover_dismiss_click_failed",
                                        extra={"url": url, "reason": str(exc)},
                                    )
                            try:
                                container.hover(timeout=DEFAULT_PROGRESSIVE_HOVER_TIMEOUT_MS)
                                return True
                            except PatchrightTimeoutError as exc:
                                logger.warning(
                                    "patchright_provider.progressive_hover_blocked",
                                    extra={"url": url, "reason": str(exc)},
                                )
                                return False

                        def _trigger_and_wait_for_feed_response(
                            trigger_fn: Callable[[], None],
                        ) -> bool:
                            """**Sixth revision (docs/REQUIREMENTS.md
                            section 9 entry 17), same real, CI-confirmed
                            correction as camoufox_provider.py's
                            identical helper:** stopping unconditionally
                            on the first timeout was itself wrong --
                            page.mouse.wheel() sometimes produces no
                            scroll event at all for reasons unrelated to
                            how much real content is left. Two real
                            signals now, preferred in this order: (1)
                            the actual /api/feed response body's own
                            page_info.has_next_page -- the authoritative
                            source of truth, not an inference from
                            timing; (2) only when no response arrives at
                            all, tolerate up to
                            DEFAULT_PROGRESSIVE_MAX_CONSECUTIVE_SCROLL_STALLS
                            consecutive timeouts before giving up. See
                            camoufox_provider.py's own identical helper
                            for the full reasoning.
                            """
                            nonlocal progressive_scroll_ended_early, consecutive_scroll_stalls
                            try:
                                with page.expect_response(
                                    lambda response: "/api/feed" in response.url
                                    and response.status == 200,
                                    timeout=DEFAULT_PROGRESSIVE_NETWORK_IDLE_TIMEOUT_MS,
                                ) as response_info:
                                    trigger_fn()
                                consecutive_scroll_stalls = 0
                                has_next_page = None
                                page_post_ids: list[str] = []
                                try:
                                    body = response_info.value.json()
                                    has_next_page = body.get("page_info", {}).get(
                                        "has_next_page"
                                    )
                                    page_post_ids = [
                                        edge["post"]["id"]
                                        for edge in body.get("edges", [])
                                        if isinstance(edge, dict)
                                        and isinstance(edge.get("post"), dict)
                                        and "id" in edge["post"]
                                    ]
                                except (ValueError, AttributeError, TypeError, KeyError) as exc:
                                    logger.debug(
                                        "patchright_provider.progressive_response_not_feed_json",
                                        extra={"url": url, "reason": str(exc)},
                                    )
                                progressive_page_post_ids.extend(page_post_ids)
                                if has_next_page is False:
                                    progressive_scroll_ended_early = True
                                    return False
                                return True
                            except PatchrightTimeoutError:
                                consecutive_scroll_stalls += 1
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
                    # Same reasoning as camoufox_provider.py's identical
                    # block (docs/REQUIREMENTS.md section 9's "DOM
                    # Virtualization Instability" investigation).
                    # **Revision, same entry 17 (real bug fix, not just
                    # camoufox_provider.py's concern):** templates/feed.html
                    # no longer sets `window.__loadMoreCalls`/
                    # `__loadMoreDropped` at all -- confirmed by hand
                    # that Camoufox/Firefox cannot read a `window.*`
                    # property back once it's set by the page's own
                    # inline `<script>` (see camoufox_provider.py's
                    # `_read_feed_attr` docstring for the full
                    # three-control-case confirmation), so the counters
                    # now live as `container`'s (`[data-role="feed"]`)
                    # `data-load-more-calls`/`data-load-more-dropped`
                    # DOM attributes instead. Patchright drives Chromium,
                    # not Firefox, so it may never have shared Camoufox's
                    # specific read-back bug -- but since the page no
                    # longer exposes the old `window.*` properties at
                    # all, this read must follow the same rename
                    # regardless, or it would silently regress to always
                    # reading 0 for an unrelated reason (the property
                    # simply not existing any more).
                    load_more_calls = (
                        page.evaluate(
                            "Number(document.querySelector('[data-role=\"feed\"]')"
                            "?.getAttribute('data-load-more-calls')) || 0"
                        )
                        if progressive_extraction
                        else None
                    )
                    load_more_dropped = (
                        page.evaluate(
                            "Number(document.querySelector('[data-role=\"feed\"]')"
                            "?.getAttribute('data-load-more-dropped')) || 0"
                        )
                        if progressive_extraction
                        else None
                    )
                    # docs/REQUIREMENTS.md section 9 entry 17, same
                    # user-review-driven addition as camoufox_provider.py's
                    # identical block.
                    progressive_api_reported_post_id_count = (
                        len(progressive_page_post_ids) if progressive_extraction else None
                    )
                    progressive_api_reported_post_id_count_unique = (
                        len(set(progressive_page_post_ids)) if progressive_extraction else None
                    )
                    final_response = last_main_frame_response or initial_response
                    content_type = (
                        final_response.headers.get("content-type", "")
                        if final_response is not None
                        else ""
                    )
                    if "application/json" in content_type:
                        # The raw network body of the *real, final*
                        # response -- sidesteps Chromium's own built-in
                        # JSON viewer wrapping the rendered DOM (see this
                        # function's docstring).
                        html = (
                            final_response.text()
                            if final_response is not None
                            else page.content()
                        )
                    else:
                        html = page.content()
                    # entry 14: same override as camoufox_provider.py's
                    # identical comment.
                    if html_snapshots:
                        html = html_snapshots[-1]
                    status = final_response.status if final_response is not None else 200
                    cookies = {c["name"]: c["value"] for c in page.context.cookies()}
                    # Same reasoning as camoufox_provider.py's identical
                    # log line: status 200 alone means nothing for a
                    # provider that solves anti-bot challenges (a
                    # challenge/deny page is routinely served as a normal
                    # 200) -- this is the real evidence of whether content
                    # actually got past the challenge.
                    logger.info(
                        "patchright_provider.solved",
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
                            # Same reasoning as camoufox_provider.py's
                            # identical field ("Fifth revision").
                            "progressive_scroll_ended_early": (
                                progressive_scroll_ended_early if progressive_extraction else None
                            ),
                            "load_more_calls": load_more_calls,
                            "load_more_dropped": load_more_dropped,
                            "progressive_api_reported_post_id_count": (
                                progressive_api_reported_post_id_count
                            ),
                            "progressive_api_reported_post_id_count_unique": (
                                progressive_api_reported_post_id_count_unique
                            ),
                        },
                    )
                    if use_accumulated_profile:
                        # docs/REQUIREMENTS.md section 9 entry 21, Step
                        # 2: same reasoning as camoufox_provider.py's
                        # identical block -- only on a genuinely
                        # successful solve, never allowed to fail the
                        # actual crawl over a jar-write problem.
                        try:
                            record_new_session(
                                cookie_jar_path,
                                context.storage_state(),  # type: ignore[arg-type]
                            )
                        # OSError: a real filesystem problem in
                        # cookie_jar_manager.py's own save_jar()/locking
                        # (a full disk, a permissions issue, the .lock
                        # file's own open()). PatchrightError: raised by
                        # context.storage_state() itself if the browser
                        # context has already gone away by this point
                        # (e.g. a crash between the solve completing and
                        # this save running) -- confirmed a real,
                        # separate exception type from OSError (not a
                        # subclass of it), so it needs its own place in
                        # this tuple, not just OSError alone.
                        except (OSError, PatchrightError) as exc:
                            logger.warning(
                                "patchright_provider.cookie_jar_save_failed",
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
                except PatchrightError as exc:
                    # docs/REQUIREMENTS.md section 9 entry 17: same
                    # BrowserCrashedError distinction as
                    # camoufox_provider.py's identical branch -- see
                    # _classify_solve_exception's own docstring.
                    raise _classify_solve_exception(browser_crashed, url, exc) from exc
            finally:
                if trace_dir is not None:
                    page.context.tracing.stop(path=build_trace_path(trace_dir, url, "patchright"))
                page.close()
        finally:
            browser.close()


class PatchrightProvider(AntibotProvider):
    """Solves anti-bot challenges by driving a real Patchright-stealthed
    Chromium directly -- the "lighter than Camoufox" option."""

    def __init__(
        self,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        post_load_wait_ms: int = DEFAULT_POST_LOAD_WAIT_MS,
        solve_fn: PatchrightSolveFn | None = None,
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
        self._solve_fn = solve_fn or _default_patchright_solve
        self.logger = logger or get_logger(__name__)
        self._max_browser_crash_attempts = max_browser_crash_attempts
        # Same reasoning as CamoufoxProvider's identical field (docs/
        # REQUIREMENTS.md section 9 entry 21, Step 2).
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
        user_agent_override: str | None = None,
    ) -> Solution:
        # docs/REQUIREMENTS.md section 9 entry 17: same bounded
        # browser-crash retry as CamoufoxProvider.solve()'s identical
        # loop -- see its own comment for the full reasoning.
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
                    user_agent_override,
                )
            except BrowserCrashedError as exc:
                if attempt >= self._max_browser_crash_attempts:
                    self.logger.error(
                        "patchright_provider.solve_failed",
                        extra={
                            "url": url,
                            "browser_crash_attempts_exhausted": attempt,
                        },
                    )
                    # Unified failure taxonomy (docs/REQUIREMENTS.md
                    # section 9 entry 28) -- same reasoning as
                    # camoufox_provider.py's identical wiring: a real
                    # browser engine crash, only once retries are
                    # exhausted, is network-infra-transient.
                    record_failure(
                        FailureRecord(
                            timestamp=datetime.now(tz=UTC),
                            target=url,
                            provider="patchright",
                            failure_category=FailureCategory.NETWORK_INFRA_TRANSIENT,
                            raw_signal={
                                "browser_crash_attempts_exhausted": attempt,
                                "reason": str(exc),
                            },
                            source="patchright_provider.solve_failed",
                        )
                    )
                    raise
                self.logger.warning(
                    "patchright_provider.browser_crash_retry",
                    extra={"url": url, "attempt": attempt, "reason": str(exc)},
                )
                continue
            except AntibotError as exc:
                self.logger.error("patchright_provider.solve_failed", extra={"url": url})
                record_failure(
                    FailureRecord(
                        timestamp=datetime.now(tz=UTC),
                        target=url,
                        provider="patchright",
                        failure_category=FailureCategory.ANTIBOT_FINGERPRINT_REJECTION,
                        raw_signal={"reason": str(exc)},
                        source="patchright_provider.solve_failed",
                    )
                )
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
