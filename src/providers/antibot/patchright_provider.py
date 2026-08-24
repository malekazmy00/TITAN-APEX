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

from src.core.exceptions import AntibotError
from src.core.interfaces.antibot_provider import AntibotProvider, LiveDomSelectors, Solution
from src.logging_config import get_logger
from src.providers.antibot._live_dom import (
    collect_live_dom_items_progressively,
    extract_live_dom_items,
)
from src.providers.antibot._scroll import collect_html_snapshots, scroll_to_load_lazy_content

DEFAULT_TIMEOUT_MS = 30_000
# Same reasoning and same default as CamoufoxProvider's
# DEFAULT_POST_LOAD_WAIT_MS (camoufox_provider.py) -- long enough for
# Anubis's own real, difficulty-2-by-default proof-of-work challenge to
# compute and round-trip.
DEFAULT_POST_LOAD_WAIT_MS = 5_000
# Same values and same reasoning as CamoufoxProvider's identical
# constants (docs/REQUIREMENTS.md section 9 entry 13).
DEFAULT_MAX_SCROLL_ATTEMPTS = 8
DEFAULT_SCROLL_PAUSE_MS = 700


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
# progressive_extraction) -> raw browser result
PatchrightSolveFn = Callable[
    [str, int, int, "str | None", "LiveDomSelectors | None", bool], _RawSolve
]


def _default_patchright_solve(
    url: str,
    timeout_ms: int,
    post_load_wait_ms: int,
    click_selector: str | None = None,
    extraction_selectors: LiveDomSelectors | None = None,
    progressive_extraction: bool = False,
) -> _RawSolve:
    """Drive a real Patchright-stealthed Chromium: navigate, (optionally)
    click, wait past ``load``, read, close.

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
    that function's own docstring for the full explanation.

    Raises:
        AntibotError: if the browser fails to launch, navigate, or read
            the page -- wraps Patchright's own launch/navigation/page
            errors (``patchright.sync_api.Error``, the same exception type
            plain Playwright raises, since Patchright is a drop-in
            replacement for it).
    """
    from patchright.sync_api import Error as PatchrightError
    from patchright.sync_api import Response as PatchrightResponse
    from patchright.sync_api import sync_playwright

    logger = get_logger(__name__)
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except PatchrightError as exc:
            raise AntibotError(
                f"patchright failed to launch chromium for {url} "
                "(run: patchright install chromium)"
            ) from exc

        try:
            page = browser.new_page()
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
                    initial_response = page.goto(url, timeout=timeout_ms)
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
                    if progressive_extraction and extraction_selectors is not None:
                        items = collect_live_dom_items_progressively(
                            page,
                            extraction_selectors.item,
                            extraction_selectors.fields,
                            DEFAULT_MAX_SCROLL_ATTEMPTS,
                            DEFAULT_SCROLL_PAUSE_MS,
                        )
                    elif progressive_extraction:
                        html_snapshots = collect_html_snapshots(
                            page, DEFAULT_MAX_SCROLL_ATTEMPTS, DEFAULT_SCROLL_PAUSE_MS
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
                    raise AntibotError(f"patchright failed to solve {url}: {exc}") from exc
            finally:
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
    ) -> None:
        if timeout_ms <= 0:
            raise AntibotError(f"timeout_ms must be > 0, got {timeout_ms}")
        if post_load_wait_ms < 0:
            raise AntibotError(f"post_load_wait_ms must be >= 0, got {post_load_wait_ms}")
        self._timeout_ms = timeout_ms
        self._post_load_wait_ms = post_load_wait_ms
        self._solve_fn = solve_fn or _default_patchright_solve
        self.logger = logger or get_logger(__name__)

    def solve(
        self,
        url: str,
        click_selector: str | None = None,
        extraction_selectors: LiveDomSelectors | None = None,
        progressive_extraction: bool = False,
    ) -> Solution:
        try:
            raw = self._solve_fn(
                url,
                self._timeout_ms,
                self._post_load_wait_ms,
                click_selector,
                extraction_selectors,
                progressive_extraction,
            )
        except AntibotError:
            self.logger.error("patchright_provider.solve_failed", extra={"url": url})
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
