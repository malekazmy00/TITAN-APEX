"""Downloader middleware: renders JavaScript-heavy pages via a headless browser.

Only requests explicitly marked ``request.meta["playwright"] = True`` are
rendered this way (set by ``GenericSpider`` when a target's config sets
``render_js: true``) — every other request passes straight through
untouched. The renderer itself, and the function that runs it off the
Twisted reactor thread, are both injectable so unit tests never launch a
real browser or touch the network.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from logging import Logger
from typing import Any, NamedTuple

from scrapy.http import HtmlResponse, Request, Response
from twisted.internet.defer import Deferred
from twisted.internet.threads import deferToThread

from src.core.exceptions import RenderError
from src.logging_config import get_logger

DEFAULT_TIMEOUT_MS = 30_000
DEFAULT_MAX_SCROLL_ATTEMPTS = 8
DEFAULT_SCROLL_PAUSE_MS = 700


class RenderedPage(NamedTuple):
    """Result of rendering one URL with a headless browser."""

    html: str
    status: int


# A precise Protocol would need to describe render_with_playwright's full
# keyword surface (executable_path, scroll tuning, ...) just to satisfy
# structural typing for the two calls that actually cross the
# PlaywrightMiddleware boundary (url, render_wait_ms, click_selector) --
# Callable[..., RenderedPage] says the same thing without that ceremony,
# and injected fakes in tests still get checked against RenderedPage.
Renderer = Callable[..., RenderedPage]
ThreadRunner = Callable[[Callable[[Request], Response], Request], Any]


def render_with_playwright(
    url: str,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    executable_path: str | None = None,
    max_scroll_attempts: int = DEFAULT_MAX_SCROLL_ATTEMPTS,
    scroll_pause_ms: int = DEFAULT_SCROLL_PAUSE_MS,
    render_wait_ms: int | None = None,
    click_selector: str | None = None,
) -> RenderedPage:
    """Default renderer: launches a real headless Chromium via Playwright.

    After the initial navigation:

    1. if ``click_selector`` is set, that element is clicked (Playwright
       scrolls it into view first) -- for content that only appears after
       an explicit interaction, e.g. a "Load More" button that a scroll
       never triggers (docs/REQUIREMENTS.md, section 7, entry 4);
    2. the page is scrolled to the bottom repeatedly (up to
       ``max_scroll_attempts`` times, pausing ``scroll_pause_ms`` between
       attempts) and stops early once the page stops growing -- this is
       what makes infinite-scroll targets actually pull in more than the
       first batch. Harmless (a couple of no-op scrolls) on a page that
       doesn't use infinite scroll;
    3. if ``render_wait_ms`` is set, one more fixed wait -- for a site
       that adds its own client-side delay *after* content has already
       arrived and before it renders, which neither of the above is
       guaranteed to cover (docs/REQUIREMENTS.md, section 7, entry 3).

    Must be called off the Twisted reactor thread (Playwright's sync API
    cannot share a thread with an already-running event loop) — see
    :func:`_default_thread_runner`.

    ``executable_path`` lets a deployment point at a specific Chromium
    binary (e.g. when the installed browser revision doesn't match what
    this Playwright version expects by default) via the
    ``TITAN_PLAYWRIGHT_EXECUTABLE`` setting — never hardcoded here.
    """
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(executable_path=executable_path)
        except PlaywrightError as exc:
            raise RenderError(f"playwright failed to launch chromium for {url}") from exc

        try:
            page = browser.new_page()
            try:
                response = page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                if click_selector:
                    page.click(click_selector, timeout=timeout_ms)
                _scroll_to_load_lazy_content(page, max_scroll_attempts, scroll_pause_ms)
                if render_wait_ms:
                    page.wait_for_timeout(render_wait_ms)
                html = page.content()
                status = response.status if response is not None else 200
                return RenderedPage(html=html, status=status)
            except PlaywrightError as exc:
                raise RenderError(f"playwright failed to render {url}") from exc
            finally:
                page.close()
        finally:
            browser.close()


def _scroll_to_load_lazy_content(page: Any, max_attempts: int, pause_ms: int) -> None:
    """Scroll to the bottom of ``page`` until its height stops growing.

    ``page`` is a ``playwright.sync_api.Page``, typed as ``Any`` here
    since Playwright ships without inline type stubs.
    """
    previous_height = page.evaluate("document.body.scrollHeight")
    for _ in range(max_attempts):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(pause_ms)
        current_height = page.evaluate("document.body.scrollHeight")
        if current_height <= previous_height:
            break
        previous_height = current_height


def _default_thread_runner(
    render_fn: Callable[[Request], Response], request: Request
) -> Deferred[Response]:
    return deferToThread(render_fn, request)


class PlaywrightMiddleware:
    """Renders ``request.meta["playwright"]``-flagged requests with a headless browser."""

    def __init__(
        self,
        renderer: Renderer | None = None,
        logger: Logger | None = None,
        thread_runner: ThreadRunner | None = None,
    ) -> None:
        self.renderer = renderer or render_with_playwright
        self.logger = logger or get_logger(__name__)
        self._thread_runner = thread_runner or _default_thread_runner

    @classmethod
    def from_crawler(cls, crawler: Any) -> PlaywrightMiddleware:
        executable_path = crawler.settings.get("TITAN_PLAYWRIGHT_EXECUTABLE") or None
        if executable_path:
            renderer: Renderer = functools.partial(
                render_with_playwright, executable_path=executable_path
            )
            return cls(renderer=renderer)
        return cls()

    def process_request(self, request: Request, spider: Any) -> Any:
        if not request.meta.get("playwright"):
            return None
        return self._thread_runner(self._render, request)

    def _render(self, request: Request) -> Response:
        try:
            page = self.renderer(
                request.url,
                render_wait_ms=request.meta.get("render_wait_ms"),
                click_selector=request.meta.get("click_selector"),
            )
        except RenderError:
            self.logger.error("playwright_middleware.render_failed", extra={"url": request.url})
            raise
        return HtmlResponse(
            url=request.url, body=page.html.encode("utf-8"), status=page.status, request=request
        )
