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

from collections.abc import Callable
from datetime import UTC, datetime
from logging import Logger
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
from src.providers.antibot._scroll import collect_html_snapshots, scroll_to_load_lazy_content

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


def _default_camoufox_solve(
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
    try:
        # camoufox ships no py.typed marker / inline stubs, so mypy sees
        # this constructor call itself (not objects it returns -- those
        # are genuine Playwright objects, typed as Any below the same
        # way playwright_middleware.py already treats them) as untyped.
        with Camoufox(headless=True) as browser:  # type: ignore[no-untyped-call]
            try:
                page = browser.new_page()
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
                    if progressive_extraction and extraction_selectors is not None:
                        items = collect_live_dom_items_progressively(
                            page,
                            extraction_selectors.item,
                            extraction_selectors.fields,
                            DEFAULT_PROGRESSIVE_MAX_SCROLL_ATTEMPTS,
                            DEFAULT_PROGRESSIVE_SCROLL_PAUSE_MS,
                        )
                    elif progressive_extraction:
                        html_snapshots = collect_html_snapshots(
                            page,
                            DEFAULT_PROGRESSIVE_MAX_SCROLL_ATTEMPTS,
                            DEFAULT_PROGRESSIVE_SCROLL_PAUSE_MS,
                        )
                    else:
                        scroll_to_load_lazy_content(
                            page, DEFAULT_MAX_SCROLL_ATTEMPTS, DEFAULT_SCROLL_PAUSE_MS
                        )
                        if extraction_selectors is not None:
                            items = extract_live_dom_items(
                                page, extraction_selectors.item, extraction_selectors.fields
                            )
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
                    page.close()
            finally:
                browser.close()
    except CamoufoxNotInstalled as exc:
        raise AntibotError(
            f"camoufox browser binary not installed for {url} "
            "(run: python -m camoufox fetch)"
        ) from exc
    except PlaywrightError as exc:
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
