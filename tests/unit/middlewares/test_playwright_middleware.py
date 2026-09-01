"""Unit tests for src/middlewares/playwright_middleware.py.

No real browser is launched here: both the renderer and the thread runner
are injected, so these tests are fast, deterministic, and network-free.
"""

from __future__ import annotations

import pytest
from scrapy.http import HtmlResponse, Request

from src.core.exceptions import RenderError
from src.middlewares.playwright_middleware import (
    PlaywrightMiddleware,
    RenderedPage,
    ScrollDiagnostics,
    _scroll_to_load_lazy_content,
    render_with_playwright,
)


class _FakePage:
    """Stands in for a playwright.sync_api.Page for _scroll_to_load_lazy_content.

    evaluate() is called for two different scripts in the real code
    (scrollTo, which we ignore, and the scrollHeight read, which consumes
    the next value from ``heights``) -- distinguished by script content,
    same as the real DOM APIs being invoked.
    """

    def __init__(self, heights: list[int]) -> None:
        self._heights = iter(heights)
        self.scroll_calls = 0
        self.wait_calls: list[int] = []

    def evaluate(self, script: str) -> int | None:
        if script.startswith("window.scrollTo"):
            return None
        return next(self._heights)

    def wait_for_timeout(self, ms: int) -> None:
        self.wait_calls.append(ms)
        self.scroll_calls += 1


def _sync_thread_runner(render_fn, request):  # type: ignore[no-untyped-def]
    """Runs the render function synchronously in-line (no real thread/Deferred)."""
    return render_fn(request)


def test_process_request_renders_a_flagged_request() -> None:
    """Happy path: a request with meta['playwright']=True is rendered and returns
    an HtmlResponse built from the renderer's output."""

    def fake_renderer(
        url: str, render_wait_ms: int | None = None, click_selector: str | None = None
    ) -> RenderedPage:
        return RenderedPage(html="<html><body>rendered</body></html>", status=200)

    middleware = PlaywrightMiddleware(renderer=fake_renderer, thread_runner=_sync_thread_runner)
    request = Request("https://example.com/", meta={"playwright": True})

    result = middleware.process_request(request, spider=object())

    assert isinstance(result, HtmlResponse)
    assert result.status == 200
    assert b"rendered" in result.body


def test_process_request_ignores_requests_without_the_playwright_flag() -> None:
    """A plain request (no meta flag) passes straight through — renderer never called."""
    calls: list[str] = []

    def fake_renderer(
        url: str, render_wait_ms: int | None = None, click_selector: str | None = None
    ) -> RenderedPage:
        calls.append(url)
        return RenderedPage(html="<html></html>", status=200)

    middleware = PlaywrightMiddleware(renderer=fake_renderer, thread_runner=_sync_thread_runner)
    request = Request("https://example.com/")

    result = middleware.process_request(request, spider=object())

    assert result is None
    assert calls == []


def test_process_request_propagates_render_error() -> None:
    """Failure case 1: a renderer failure surfaces as RenderError, never swallowed."""

    def failing_renderer(
        url: str, render_wait_ms: int | None = None, click_selector: str | None = None
    ) -> RenderedPage:
        raise RenderError(f"boom rendering {url}")

    middleware = PlaywrightMiddleware(renderer=failing_renderer, thread_runner=_sync_thread_runner)
    request = Request("https://example.com/", meta={"playwright": True})

    with pytest.raises(RenderError, match="boom rendering"):
        middleware.process_request(request, spider=object())


def test_process_request_logs_before_reraising(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure case 2: the failure is logged (not silently re-raised) before propagating."""
    logged: list[str] = []

    class _FakeLogger:
        def error(self, msg: str, extra: dict[str, object] | None = None) -> None:
            logged.append(msg)

        def warning(self, msg: str, extra: dict[str, object] | None = None) -> None:
            pass

    def failing_renderer(
        url: str, render_wait_ms: int | None = None, click_selector: str | None = None
    ) -> RenderedPage:
        raise RenderError("render failed")

    middleware = PlaywrightMiddleware(
        renderer=failing_renderer,
        thread_runner=_sync_thread_runner,
        logger=_FakeLogger(),  # type: ignore[arg-type]
    )
    request = Request("https://example.com/", meta={"playwright": True})

    with pytest.raises(RenderError):
        middleware.process_request(request, spider=object())

    assert "playwright_middleware.render_failed" in logged


def test_render_wait_ms_and_click_selector_reach_the_renderer_from_meta() -> None:
    """Happy path: render_wait_ms/click_selector set in request.meta (i.e. by
    GenericSpider from a target's config) are passed through to the renderer call,
    not silently dropped."""
    received: dict[str, object] = {}

    def fake_renderer(
        url: str, render_wait_ms: int | None = None, click_selector: str | None = None
    ) -> RenderedPage:
        received["render_wait_ms"] = render_wait_ms
        received["click_selector"] = click_selector
        return RenderedPage(html="<html></html>", status=200)

    middleware = PlaywrightMiddleware(renderer=fake_renderer, thread_runner=_sync_thread_runner)
    request = Request(
        "https://example.com/",
        meta={"playwright": True, "render_wait_ms": 2500, "click_selector": "button.load-more"},
    )

    middleware.process_request(request, spider=object())

    assert received == {"render_wait_ms": 2500, "click_selector": "button.load-more"}


def test_render_wait_ms_and_click_selector_default_to_none() -> None:
    """A request whose meta never set render_wait_ms/click_selector (every target
    before this feature existed, and every render_js target that doesn't need it)
    must not regress -- both must reach the renderer as None."""
    received: dict[str, object] = {}

    def fake_renderer(
        url: str, render_wait_ms: int | None = None, click_selector: str | None = None
    ) -> RenderedPage:
        received["render_wait_ms"] = render_wait_ms
        received["click_selector"] = click_selector
        return RenderedPage(html="<html></html>", status=200)

    middleware = PlaywrightMiddleware(renderer=fake_renderer, thread_runner=_sync_thread_runner)
    request = Request("https://example.com/", meta={"playwright": True})

    middleware.process_request(request, spider=object())

    assert received == {"render_wait_ms": None, "click_selector": None}


def test_from_crawler_builds_default_instance() -> None:
    class _FakeSettings:
        def get(self, name: str, default: object = None) -> object:
            return default

    class _FakeCrawler:
        settings = _FakeSettings()

    middleware = PlaywrightMiddleware.from_crawler(crawler=_FakeCrawler())
    assert isinstance(middleware, PlaywrightMiddleware)
    assert middleware.renderer is render_with_playwright


def test_from_crawler_binds_a_configured_executable_path() -> None:
    """TITAN_PLAYWRIGHT_EXECUTABLE lets a deployment point at a specific Chromium
    binary without hardcoding the path in code."""

    class _FakeSettings:
        def get(self, name: str, default: object = None) -> object:
            return {"TITAN_PLAYWRIGHT_EXECUTABLE": "/opt/pw-browsers/chromium"}.get(
                name, default
            )

    class _FakeCrawler:
        settings = _FakeSettings()

    middleware = PlaywrightMiddleware.from_crawler(crawler=_FakeCrawler())

    assert middleware.renderer is not render_with_playwright
    assert middleware.renderer.func is render_with_playwright  # type: ignore[union-attr]
    assert middleware.renderer.keywords == {  # type: ignore[union-attr]
        "executable_path": "/opt/pw-browsers/chromium"
    }


def test_scroll_stops_once_page_height_stops_growing() -> None:
    """Happy path: this is the actual fix for the bug a live CI run caught --
    render_with_playwright() previously never scrolled at all, so infinite-scroll
    targets only ever returned their first static batch. Growth for 2 scrolls,
    then flat -> stop after 3 evaluate() height reads (initial + 2 scrolls),
    not the full max_attempts."""
    page = _FakePage(heights=[1000, 1500, 2000, 2000])

    diagnostics = _scroll_to_load_lazy_content(page, max_attempts=8, pause_ms=100)

    assert page.scroll_calls == 3
    assert page.wait_calls == [100, 100, 100]
    assert diagnostics == ScrollDiagnostics(
        attempts_used=3, initial_height=1000, final_height=2000
    )


def test_scroll_stops_immediately_on_a_page_with_no_lazy_content() -> None:
    """Failure-adjacent case 1: a page whose height never changes (no infinite
    scroll) must not waste extra scroll attempts."""
    page = _FakePage(heights=[500, 500])

    diagnostics = _scroll_to_load_lazy_content(page, max_attempts=8, pause_ms=50)

    assert page.scroll_calls == 1
    assert diagnostics == ScrollDiagnostics(attempts_used=1, initial_height=500, final_height=500)


def test_scroll_stops_at_max_attempts_if_the_page_never_stabilizes() -> None:
    """Failure-adjacent case 2: a page that keeps growing forever must not loop
    forever -- max_attempts is a hard cap."""
    ever_growing_heights = [100 * i for i in range(1, 12)]  # far more than max_attempts
    page = _FakePage(heights=ever_growing_heights)

    diagnostics = _scroll_to_load_lazy_content(page, max_attempts=5, pause_ms=10)

    assert page.scroll_calls == 5
    assert diagnostics == ScrollDiagnostics(attempts_used=5, initial_height=100, final_height=600)


def test_render_logs_scroll_diagnostics_when_present() -> None:
    """Happy path (docs/REQUIREMENTS.md section 9 entry 25): a renderer that
    returns real scroll diagnostics gets them logged as a structured line --
    the whole point of building this detection mechanism is that a future
    investigation can read this straight out of the CI log instead of
    re-deriving it from scratch."""
    logged: list[tuple[str, dict[str, object]]] = []

    class _FakeLogger:
        def error(self, msg: str, extra: dict[str, object] | None = None) -> None:
            pass

        def warning(self, msg: str, extra: dict[str, object] | None = None) -> None:
            pass

        def info(self, msg: str, extra: dict[str, object] | None = None) -> None:
            logged.append((msg, extra or {}))

    def fake_renderer(
        url: str, render_wait_ms: int | None = None, click_selector: str | None = None
    ) -> RenderedPage:
        return RenderedPage(
            html="<html></html>",
            status=200,
            scroll_diagnostics=ScrollDiagnostics(
                attempts_used=8, initial_height=1200, final_height=1200, requests_during_scroll=0
            ),
        )

    middleware = PlaywrightMiddleware(
        renderer=fake_renderer,
        thread_runner=_sync_thread_runner,
        logger=_FakeLogger(),  # type: ignore[arg-type]
    )
    request = Request("https://example.com/", meta={"playwright": True})

    middleware.process_request(request, spider=object())

    assert len(logged) == 1
    msg, extra = logged[0]
    assert msg == "playwright_middleware.scroll_diagnostics"
    assert extra == {
        "url": "https://example.com/",
        "attempts_used": 8,
        "initial_height": 1200,
        "final_height": 1200,
        "requests_during_scroll": 0,
    }


def test_render_does_not_log_scroll_diagnostics_when_absent() -> None:
    """Failure-adjacent case (backward compatibility): a renderer that never
    sets scroll_diagnostics (RenderedPage's own default) must not log a
    misleading diagnostics line -- "we never checked" must stay
    distinguishable from "we checked and got a real snapshot"."""
    logged: list[str] = []

    class _FakeLogger:
        def error(self, msg: str, extra: dict[str, object] | None = None) -> None:
            pass

        def warning(self, msg: str, extra: dict[str, object] | None = None) -> None:
            pass

        def info(self, msg: str, extra: dict[str, object] | None = None) -> None:
            logged.append(msg)

    def fake_renderer(
        url: str, render_wait_ms: int | None = None, click_selector: str | None = None
    ) -> RenderedPage:
        return RenderedPage(html="<html></html>", status=200)

    middleware = PlaywrightMiddleware(
        renderer=fake_renderer,
        thread_runner=_sync_thread_runner,
        logger=_FakeLogger(),  # type: ignore[arg-type]
    )
    request = Request("https://example.com/", meta={"playwright": True})

    middleware.process_request(request, spider=object())

    assert logged == []
