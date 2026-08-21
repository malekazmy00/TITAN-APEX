"""Unit tests for src/middlewares/byparr_middleware.py."""

from __future__ import annotations

from datetime import UTC, datetime

from scrapy.http import HtmlResponse, Request

from src.core.exceptions import AntibotError
from src.core.interfaces.antibot_provider import AntibotProvider, Solution
from src.middlewares.byparr_middleware import ByparrMiddleware


class _FakeProvider(AntibotProvider):
    def __init__(self, solution: Solution | None = None, error: AntibotError | None = None) -> None:
        self._solution = solution
        self._error = error

    def solve(self, url: str) -> Solution:
        if self._error is not None:
            raise self._error
        assert self._solution is not None
        return self._solution


def test_process_request_returns_response_with_cookies_for_flagged_request() -> None:
    """Happy path: a solved page comes back as an HtmlResponse with Set-Cookie headers,
    so Scrapy's own CookiesMiddleware will pick them up automatically."""
    solution = Solution(
        url="https://example.com/",
        html="<html>solved</html>",
        status_code=200,
        cookies={"session": "abc", "cf_clearance": "xyz"},
        solved_at=datetime.now(tz=UTC),
    )
    middleware = ByparrMiddleware(provider=_FakeProvider(solution=solution))
    request = Request("https://example.com/", meta={"antibot_needed": True})

    result = middleware.process_request(request, spider=object())

    assert isinstance(result, HtmlResponse)
    assert result.status == 200
    assert b"solved" in result.body
    set_cookie_headers = {h.decode() for h in result.headers.getlist("Set-Cookie")}
    assert set_cookie_headers == {"session=abc", "cf_clearance=xyz"}


def test_process_request_ignores_requests_without_the_flag() -> None:
    middleware = ByparrMiddleware(provider=_FakeProvider())
    request = Request("https://example.com/")

    result = middleware.process_request(request, spider=object())

    assert result is None


def test_process_request_falls_back_when_no_provider_configured() -> None:
    """Failure case 1: an unconfigured Byparr must fall back (not crash), and log why."""
    logged: list[str] = []

    class _FakeLogger:
        def error(self, msg: str, extra: dict[str, object] | None = None) -> None:
            pass

        def warning(self, msg: str, extra: dict[str, object] | None = None) -> None:
            logged.append(msg)

    middleware = ByparrMiddleware(provider=None, logger=_FakeLogger())  # type: ignore[arg-type]
    request = Request("https://example.com/", meta={"antibot_needed": True})

    result = middleware.process_request(request, spider=object())

    assert result is None  # fallback to the normal downloader, no exception raised
    assert logged == ["byparr_middleware.not_configured_fallback"]


def test_process_request_falls_back_and_logs_when_provider_fails() -> None:
    """Failure case 2: a provider failure must fall back (not crash), and log clearly."""
    logged: list[tuple[str, dict[str, object]]] = []

    class _FakeLogger:
        def error(self, msg: str, extra: dict[str, object] | None = None) -> None:
            logged.append((msg, extra or {}))

        def warning(self, msg: str, extra: dict[str, object] | None = None) -> None:
            pass

    middleware = ByparrMiddleware(
        provider=_FakeProvider(error=AntibotError("challenge unsolvable")),
        logger=_FakeLogger(),  # type: ignore[arg-type]
    )
    request = Request("https://example.com/", meta={"antibot_needed": True})

    result = middleware.process_request(request, spider=object())

    assert result is None
    assert logged
    message, extra = logged[0]
    assert message == "byparr_middleware.solve_failed_fallback"
    assert "challenge unsolvable" in str(extra["reason"])


def test_from_crawler_without_url_builds_middleware_with_no_provider() -> None:
    class _FakeSettings:
        def get(self, name: str, default: object = None) -> object:
            return default

    class _FakeCrawler:
        settings = _FakeSettings()

    middleware = ByparrMiddleware.from_crawler(_FakeCrawler())

    assert middleware.provider is None


def test_from_crawler_with_url_builds_a_real_provider() -> None:
    class _FakeSettings:
        def get(self, name: str, default: object = None) -> object:
            return {"TITAN_BYPARR_URL": "http://localhost:8191"}.get(name, default)

    class _FakeCrawler:
        settings = _FakeSettings()

    middleware = ByparrMiddleware.from_crawler(_FakeCrawler())

    assert middleware.provider is not None
