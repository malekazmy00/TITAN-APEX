"""Unit tests for src/middlewares/byparr_middleware.py."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
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
    middleware = ByparrMiddleware(byparr_provider=_FakeProvider(solution=solution))
    request = Request("https://example.com/", meta={"antibot_needed": True})

    result = middleware.process_request(request, spider=object())

    assert isinstance(result, HtmlResponse)
    assert result.status == 200
    assert b"solved" in result.body
    set_cookie_headers = {h.decode() for h in result.headers.getlist("Set-Cookie")}
    assert set_cookie_headers == {"session=abc", "cf_clearance=xyz"}


def test_process_request_ignores_requests_without_the_flag() -> None:
    middleware = ByparrMiddleware(byparr_provider=_FakeProvider())
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

    middleware = ByparrMiddleware(byparr_provider=None, logger=_FakeLogger())  # type: ignore[arg-type]
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
        byparr_provider=_FakeProvider(error=AntibotError("challenge unsolvable")),
        logger=_FakeLogger(),  # type: ignore[arg-type]
    )
    request = Request("https://example.com/", meta={"antibot_needed": True})

    result = middleware.process_request(request, spider=object())

    assert result is None
    assert logged
    message, extra = logged[0]
    assert message == "byparr_middleware.solve_failed_fallback"
    assert "challenge unsolvable" in str(extra["reason"])


def test_process_request_routes_to_camoufox_when_selected() -> None:
    """A request with antibot_provider='camoufox' must be solved by the
    camoufox provider, not byparr -- the whole point of selection."""
    byparr_solution = Solution(
        url="https://example.com/",
        html="<html>byparr</html>",
        status_code=200,
        cookies={},
        solved_at=datetime.now(tz=UTC),
    )
    camoufox_solution = Solution(
        url="https://example.com/",
        html="<html>camoufox</html>",
        status_code=200,
        cookies={},
        solved_at=datetime.now(tz=UTC),
    )
    middleware = ByparrMiddleware(
        byparr_provider=_FakeProvider(solution=byparr_solution),
        camoufox_provider=_FakeProvider(solution=camoufox_solution),
    )
    request = Request(
        "https://example.com/", meta={"antibot_needed": True, "antibot_provider": "camoufox"}
    )

    result = middleware.process_request(request, spider=object())

    assert isinstance(result, HtmlResponse)
    assert b"camoufox" in result.body


def test_process_request_defaults_to_byparr_when_provider_not_set_in_meta() -> None:
    byparr_solution = Solution(
        url="https://example.com/",
        html="<html>byparr</html>",
        status_code=200,
        cookies={},
        solved_at=datetime.now(tz=UTC),
    )
    middleware = ByparrMiddleware(
        byparr_provider=_FakeProvider(solution=byparr_solution),
        camoufox_provider=_FakeProvider(error=AntibotError("should never be called")),
    )
    request = Request("https://example.com/", meta={"antibot_needed": True})

    result = middleware.process_request(request, spider=object())

    assert isinstance(result, HtmlResponse)
    assert b"byparr" in result.body


def test_process_request_falls_back_on_unknown_provider_name() -> None:
    """Failure case 3: a typo'd/unsupported antibot_provider value must
    fall back cleanly (not KeyError), logged with the offending name."""
    logged: list[tuple[str, dict[str, object]]] = []

    class _FakeLogger:
        def error(self, msg: str, extra: dict[str, object] | None = None) -> None:
            pass

        def warning(self, msg: str, extra: dict[str, object] | None = None) -> None:
            logged.append((msg, extra or {}))

    middleware = ByparrMiddleware(
        byparr_provider=_FakeProvider(), logger=_FakeLogger()  # type: ignore[arg-type]
    )
    request = Request(
        "https://example.com/", meta={"antibot_needed": True, "antibot_provider": "nope"}
    )

    result = middleware.process_request(request, spider=object())

    assert result is None
    message, extra = logged[0]
    assert message == "byparr_middleware.not_configured_fallback"
    assert extra["provider"] == "nope"


def test_from_crawler_without_url_builds_middleware_with_no_byparr_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TITAN_BYPARR_URL", raising=False)

    class _FakeSettings:
        def get(self, name: str, default: object = None) -> object:
            return default

    class _FakeCrawler:
        settings = _FakeSettings()

    middleware = ByparrMiddleware.from_crawler(_FakeCrawler())

    assert middleware.provider is None


def test_from_crawler_always_builds_a_camoufox_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Camoufox needs no external service/URL, so it's always available,
    even when Byparr isn't configured at all."""
    monkeypatch.delenv("TITAN_BYPARR_URL", raising=False)

    class _FakeSettings:
        def get(self, name: str, default: object = None) -> object:
            return default

    class _FakeCrawler:
        settings = _FakeSettings()

    middleware = ByparrMiddleware.from_crawler(_FakeCrawler())

    assert middleware._providers["camoufox"] is not None


def test_from_crawler_with_url_builds_a_real_byparr_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TITAN_BYPARR_URL", raising=False)

    class _FakeSettings:
        def get(self, name: str, default: object = None) -> object:
            return {"TITAN_BYPARR_URL": "http://localhost:8191"}.get(name, default)

    class _FakeCrawler:
        settings = _FakeSettings()

    middleware = ByparrMiddleware.from_crawler(_FakeCrawler())

    assert middleware.provider is not None


def test_from_crawler_falls_back_to_the_os_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """crawler.settings never auto-picks up TITAN_BYPARR_URL (it only reads
    OS env vars prefixed SCRAPY_) -- a real `scrapy runspider` invocation
    with only the environment variable set (this project's CI job-level
    env, or a real deploy) must still configure a provider."""
    monkeypatch.setenv("TITAN_BYPARR_URL", "http://localhost:8191")

    class _FakeSettings:
        def get(self, name: str, default: object = None) -> object:
            return default  # crawler.settings knows nothing about it.

    class _FakeCrawler:
        settings = _FakeSettings()

    middleware = ByparrMiddleware.from_crawler(_FakeCrawler())

    assert middleware.provider is not None
