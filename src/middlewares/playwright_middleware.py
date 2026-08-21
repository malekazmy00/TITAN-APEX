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


class RenderedPage(NamedTuple):
    """Result of rendering one URL with a headless browser."""

    html: str
    status: int


Renderer = Callable[[str], RenderedPage]
ThreadRunner = Callable[[Callable[[Request], Response], Request], Any]


def render_with_playwright(
    url: str, timeout_ms: int = DEFAULT_TIMEOUT_MS, executable_path: str | None = None
) -> RenderedPage:
    """Default renderer: launches a real headless Chromium via Playwright.

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
                html = page.content()
                status = response.status if response is not None else 200
                return RenderedPage(html=html, status=status)
            except PlaywrightError as exc:
                raise RenderError(f"playwright failed to render {url}") from exc
            finally:
                page.close()
        finally:
            browser.close()


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
            page = self.renderer(request.url)
        except RenderError:
            self.logger.error("playwright_middleware.render_failed", extra={"url": request.url})
            raise
        return HtmlResponse(
            url=request.url, body=page.html.encode("utf-8"), status=page.status, request=request
        )
