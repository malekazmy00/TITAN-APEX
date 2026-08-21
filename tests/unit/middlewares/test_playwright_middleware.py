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
    render_with_playwright,
)


def _sync_thread_runner(render_fn, request):  # type: ignore[no-untyped-def]
    """Runs the render function synchronously in-line (no real thread/Deferred)."""
    return render_fn(request)


def test_process_request_renders_a_flagged_request() -> None:
    """Happy path: a request with meta['playwright']=True is rendered and returns
    an HtmlResponse built from the renderer's output."""

    def fake_renderer(url: str) -> RenderedPage:
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

    def fake_renderer(url: str) -> RenderedPage:
        calls.append(url)
        return RenderedPage(html="<html></html>", status=200)

    middleware = PlaywrightMiddleware(renderer=fake_renderer, thread_runner=_sync_thread_runner)
    request = Request("https://example.com/")

    result = middleware.process_request(request, spider=object())

    assert result is None
    assert calls == []


def test_process_request_propagates_render_error() -> None:
    """Failure case 1: a renderer failure surfaces as RenderError, never swallowed."""

    def failing_renderer(url: str) -> RenderedPage:
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

    def failing_renderer(url: str) -> RenderedPage:
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
    assert middleware.renderer.func is render_with_playwright  # type: ignore[attr-defined]
    assert middleware.renderer.keywords == {  # type: ignore[attr-defined]
        "executable_path": "/opt/pw-browsers/chromium"
    }
