"""Unit tests for src/middlewares/byparr_middleware.py.

No real thread/Deferred is used here: the thread runner is injected
(``_sync_thread_runner``), the same idiom test_playwright_middleware.py
already uses for the identical reason (Playwright's sync API -- which
CamoufoxProvider now also drives -- cannot share a thread with Scrapy's
own asyncio reactor, docs/REQUIREMENTS.md section 9 entry 5).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from scrapy.http import HtmlResponse, Request

from src.core.exceptions import AntibotError
from src.core.interfaces.antibot_provider import (
    AntibotProvider,
    LiveDomSelectors,
    LoginFlow,
    Solution,
)
from src.middlewares.byparr_middleware import ByparrMiddleware


def _sync_thread_runner(solve_fn, request):  # type: ignore[no-untyped-def]
    """Runs the solve function synchronously in-line (no real thread/Deferred)."""
    return solve_fn(request)


class _FakeProvider(AntibotProvider):
    def __init__(self, solution: Solution | None = None, error: AntibotError | None = None) -> None:
        self._solution = solution
        self._error = error
        self.last_click_selector: str | None = None
        self.last_extraction_selectors: LiveDomSelectors | None = None
        self.last_progressive_extraction: bool = False
        self.last_login_flow: LoginFlow | None = None
        self.last_warm_session_urls: list[str] | None = None
        self.last_use_accumulated_profile: bool = False
        self.last_user_agent_override: str | None = None

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
        self.last_click_selector = click_selector
        self.last_extraction_selectors = extraction_selectors
        self.last_progressive_extraction = progressive_extraction
        self.last_login_flow = login_flow
        self.last_warm_session_urls = warm_session_urls
        self.last_use_accumulated_profile = use_accumulated_profile
        self.last_user_agent_override = user_agent_override
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
    middleware = ByparrMiddleware(
        byparr_provider=_FakeProvider(solution=solution), thread_runner=_sync_thread_runner
    )
    request = Request("https://example.com/", meta={"antibot_needed": True})

    result = middleware.process_request(request, spider=object())

    assert isinstance(result, HtmlResponse)
    assert result.status == 200
    assert b"solved" in result.body
    set_cookie_headers = {h.decode() for h in result.headers.getlist("Set-Cookie")}
    assert set_cookie_headers == {"session=abc", "cf_clearance=xyz"}


def test_process_request_ignores_requests_without_the_flag() -> None:
    middleware = ByparrMiddleware(
        byparr_provider=_FakeProvider(), thread_runner=_sync_thread_runner
    )
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

    middleware = ByparrMiddleware(
        byparr_provider=None, logger=_FakeLogger(), thread_runner=_sync_thread_runner  # type: ignore[arg-type]
    )
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
        thread_runner=_sync_thread_runner,
    )
    request = Request("https://example.com/", meta={"antibot_needed": True})

    result = middleware.process_request(request, spider=object())

    assert result is None
    assert logged
    message, extra = logged[0]
    assert message == "byparr_middleware.solve_failed_fallback"
    assert "challenge unsolvable" in str(extra["reason"])


def test_process_request_passes_click_selector_from_meta_to_the_provider() -> None:
    """docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md's cookie-consent-wall
    round: request.meta["click_selector"] (already set by GenericSpider
    for every request) must reach whichever provider is selected."""
    solution = Solution(
        url="https://example.com/",
        html="<html>solved</html>",
        status_code=200,
        cookies={},
        solved_at=datetime.now(tz=UTC),
    )
    fake_provider = _FakeProvider(solution=solution)
    middleware = ByparrMiddleware(byparr_provider=fake_provider, thread_runner=_sync_thread_runner)
    request = Request(
        "https://example.com/",
        meta={"antibot_needed": True, "click_selector": "#accept-cookies"},
    )

    middleware.process_request(request, spider=object())

    assert fake_provider.last_click_selector == "#accept-cookies"


def test_process_request_passes_extraction_selectors_from_meta_to_the_provider() -> None:
    """docs/REQUIREMENTS.md section 9 entry 12:
    request.meta["extraction_selectors"] (set by GenericSpider when
    extraction_mode: "live_dom") must reach whichever provider is
    selected, same as click_selector's identical contract."""
    solution = Solution(
        url="https://example.com/",
        html="<html>solved</html>",
        status_code=200,
        cookies={},
        solved_at=datetime.now(tz=UTC),
    )
    fake_provider = _FakeProvider(solution=solution)
    middleware = ByparrMiddleware(byparr_provider=fake_provider, thread_runner=_sync_thread_runner)
    selectors = LiveDomSelectors(item='[data-role="post"]', fields={"author": "::text"})
    request = Request(
        "https://example.com/",
        meta={"antibot_needed": True, "extraction_selectors": selectors},
    )

    middleware.process_request(request, spider=object())

    assert fake_provider.last_extraction_selectors == selectors


def test_process_request_attaches_live_dom_items_when_the_provider_extracted_them() -> None:
    """The whole point of entry 12: items a provider extracted live must
    reach GenericSpider's parse() via response.meta -- a plain passthrough
    to the same request object mutated here."""
    solution = Solution(
        url="https://example.com/",
        html="<html>solved</html>",
        status_code=200,
        cookies={},
        items=[{"author": "alice"}, {"author": "bob"}],
        solved_at=datetime.now(tz=UTC),
    )
    middleware = ByparrMiddleware(
        byparr_provider=_FakeProvider(solution=solution), thread_runner=_sync_thread_runner
    )
    request = Request("https://example.com/", meta={"antibot_needed": True})

    result = middleware.process_request(request, spider=object())

    assert isinstance(result, HtmlResponse)
    assert result.meta["live_dom_items"] == [{"author": "alice"}, {"author": "bob"}]


def test_process_request_does_not_set_live_dom_items_when_the_provider_did_not_extract() -> None:
    """Failure-adjacent case: a provider that never performed live-DOM
    extraction (Solution.items is None, e.g. Byparr, or a real-browser
    provider used without extraction_mode: "live_dom") must leave
    request.meta untouched -- generic_spider.py's own
    `response.meta.get("live_dom_items")` relies on the key being genuinely
    absent, not present-but-None, to fall back to parsing html correctly
    either way, but this confirms the middleware itself never sets it
    needlessly."""
    solution = Solution(
        url="https://example.com/",
        html="<html>solved</html>",
        status_code=200,
        cookies={},
        solved_at=datetime.now(tz=UTC),
    )
    middleware = ByparrMiddleware(
        byparr_provider=_FakeProvider(solution=solution), thread_runner=_sync_thread_runner
    )
    request = Request("https://example.com/", meta={"antibot_needed": True})

    result = middleware.process_request(request, spider=object())

    assert isinstance(result, HtmlResponse)
    assert "live_dom_items" not in result.meta


def test_process_request_passes_progressive_extraction_from_meta_to_the_provider() -> None:
    """docs/REQUIREMENTS.md section 9 entry 14:
    request.meta["progressive_extraction"] (set by GenericSpider when
    SpiderConfig.progressive_extraction is true) must reach whichever
    provider is selected, same as extraction_selectors' identical
    contract."""
    solution = Solution(
        url="https://example.com/",
        html="<html>solved</html>",
        status_code=200,
        cookies={},
        solved_at=datetime.now(tz=UTC),
    )
    fake_provider = _FakeProvider(solution=solution)
    middleware = ByparrMiddleware(byparr_provider=fake_provider, thread_runner=_sync_thread_runner)
    request = Request(
        "https://example.com/",
        meta={"antibot_needed": True, "progressive_extraction": True},
    )

    middleware.process_request(request, spider=object())

    assert fake_provider.last_progressive_extraction is True


def test_process_request_passes_login_flow_from_meta_to_the_provider() -> None:
    """docs/REQUIREMENTS.md section 9 entry 15:
    request.meta["login_flow"] (set by GenericSpider when
    SpiderConfig.login is set) must reach whichever provider is
    selected, same passthrough contract as every other optional
    capability here."""
    solution = Solution(
        url="https://example.com/",
        html="<html>solved</html>",
        status_code=200,
        cookies={},
        solved_at=datetime.now(tz=UTC),
    )
    fake_provider = _FakeProvider(solution=solution)
    middleware = ByparrMiddleware(byparr_provider=fake_provider, thread_runner=_sync_thread_runner)
    login_flow = LoginFlow(
        login_url="https://example.com/login",
        username="titan_test_user",
        password="titan_test_pass",
        username_field="#username",
        password_field="#password",
        submit_selector="#login-submit",
    )
    request = Request(
        "https://example.com/",
        meta={"antibot_needed": True, "login_flow": login_flow},
    )

    middleware.process_request(request, spider=object())

    assert fake_provider.last_login_flow == login_flow


def test_process_request_passes_warm_session_urls_from_meta_to_the_provider() -> None:
    """docs/REQUIREMENTS.md section 9 entry 21, Step 2:
    request.meta["warm_session_urls"] (set by GenericSpider from
    SpiderConfig.warm_session_urls) must reach whichever provider is
    selected, same passthrough contract as every other optional
    capability here."""
    solution = Solution(
        url="https://example.com/",
        html="<html>solved</html>",
        status_code=200,
        cookies={},
        solved_at=datetime.now(tz=UTC),
    )
    fake_provider = _FakeProvider(solution=solution)
    middleware = ByparrMiddleware(byparr_provider=fake_provider, thread_runner=_sync_thread_runner)
    request = Request(
        "https://example.com/",
        meta={
            "antibot_needed": True,
            "warm_session_urls": ["https://example.com/", "https://example.com/category"],
        },
    )

    middleware.process_request(request, spider=object())

    assert fake_provider.last_warm_session_urls == [
        "https://example.com/",
        "https://example.com/category",
    ]


def test_process_request_passes_use_accumulated_profile_from_meta_to_the_provider() -> None:
    """docs/REQUIREMENTS.md section 9 entry 21, Step 2:
    request.meta["use_accumulated_profile"] (set by GenericSpider from
    SpiderConfig.use_accumulated_profile) must reach whichever provider
    is selected, same passthrough contract as every other optional
    capability here."""
    solution = Solution(
        url="https://example.com/",
        html="<html>solved</html>",
        status_code=200,
        cookies={},
        solved_at=datetime.now(tz=UTC),
    )
    fake_provider = _FakeProvider(solution=solution)
    middleware = ByparrMiddleware(byparr_provider=fake_provider, thread_runner=_sync_thread_runner)
    request = Request(
        "https://example.com/",
        meta={"antibot_needed": True, "use_accumulated_profile": True},
    )

    middleware.process_request(request, spider=object())

    assert fake_provider.last_use_accumulated_profile is True


def test_process_request_defaults_use_accumulated_profile_to_false_when_absent_from_meta() -> None:
    """Backward compatible: every existing request.meta (no such key at
    all) must keep getting the safe, isolated default."""
    solution = Solution(
        url="https://example.com/",
        html="<html>solved</html>",
        status_code=200,
        cookies={},
        solved_at=datetime.now(tz=UTC),
    )
    fake_provider = _FakeProvider(solution=solution)
    middleware = ByparrMiddleware(byparr_provider=fake_provider, thread_runner=_sync_thread_runner)
    request = Request("https://example.com/", meta={"antibot_needed": True})

    middleware.process_request(request, spider=object())

    assert fake_provider.last_use_accumulated_profile is False
    assert fake_provider.last_warm_session_urls is None


def test_process_request_attaches_html_snapshots_when_the_provider_captured_them() -> None:
    """The "parsed_html" progressive-collection half of entry 14: multiple
    HTML snapshots a provider captured must reach GenericSpider's parse()
    via response.meta, same passthrough shape as live_dom_items."""
    solution = Solution(
        url="https://example.com/",
        html="<html>solved</html>",
        status_code=200,
        cookies={},
        html_snapshots=["<html>step1</html>", "<html>step2</html>"],
        solved_at=datetime.now(tz=UTC),
    )
    middleware = ByparrMiddleware(
        byparr_provider=_FakeProvider(solution=solution), thread_runner=_sync_thread_runner
    )
    request = Request("https://example.com/", meta={"antibot_needed": True})

    result = middleware.process_request(request, spider=object())

    assert isinstance(result, HtmlResponse)
    assert result.meta["html_snapshots"] == ["<html>step1</html>", "<html>step2</html>"]


def test_process_request_does_not_set_html_snapshots_when_none_were_captured() -> None:
    """Failure-adjacent case: mirrors
    test_process_request_does_not_set_live_dom_items_when_the_provider_did_not_extract's
    identical reasoning, for html_snapshots instead."""
    solution = Solution(
        url="https://example.com/",
        html="<html>solved</html>",
        status_code=200,
        cookies={},
        solved_at=datetime.now(tz=UTC),
    )
    middleware = ByparrMiddleware(
        byparr_provider=_FakeProvider(solution=solution), thread_runner=_sync_thread_runner
    )
    request = Request("https://example.com/", meta={"antibot_needed": True})

    result = middleware.process_request(request, spider=object())

    assert isinstance(result, HtmlResponse)
    assert "html_snapshots" not in result.meta


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
        thread_runner=_sync_thread_runner,
    )
    request = Request(
        "https://example.com/", meta={"antibot_needed": True, "antibot_provider": "camoufox"}
    )

    result = middleware.process_request(request, spider=object())

    assert isinstance(result, HtmlResponse)
    assert b"camoufox" in result.body


def test_process_request_routes_to_patchright_when_selected() -> None:
    """A request with antibot_provider='patchright' must be solved by the
    patchright provider, not byparr/camoufox -- the third selectable option."""
    byparr_solution = Solution(
        url="https://example.com/",
        html="<html>byparr</html>",
        status_code=200,
        cookies={},
        solved_at=datetime.now(tz=UTC),
    )
    patchright_solution = Solution(
        url="https://example.com/",
        html="<html>patchright</html>",
        status_code=200,
        cookies={},
        solved_at=datetime.now(tz=UTC),
    )
    middleware = ByparrMiddleware(
        byparr_provider=_FakeProvider(solution=byparr_solution),
        patchright_provider=_FakeProvider(solution=patchright_solution),
        thread_runner=_sync_thread_runner,
    )
    request = Request(
        "https://example.com/", meta={"antibot_needed": True, "antibot_provider": "patchright"}
    )

    result = middleware.process_request(request, spider=object())

    assert isinstance(result, HtmlResponse)
    assert b"patchright" in result.body


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
        thread_runner=_sync_thread_runner,
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
        byparr_provider=_FakeProvider(),
        logger=_FakeLogger(),  # type: ignore[arg-type]
        thread_runner=_sync_thread_runner,
    )
    request = Request(
        "https://example.com/", meta={"antibot_needed": True, "antibot_provider": "nope"}
    )

    result = middleware.process_request(request, spider=object())

    assert result is None
    message, extra = logged[0]
    assert message == "byparr_middleware.not_configured_fallback"
    assert extra["provider"] == "nope"


def test_process_request_defaults_to_a_real_thread_runner() -> None:
    """No thread_runner injected -- process_request must actually hand off
    to deferToThread (a real Deferred, not the solved Response directly),
    the whole reason this middleware runs solving off the reactor thread
    at all (docs/REQUIREMENTS.md section 9 entry 5)."""
    from twisted.internet.defer import Deferred

    middleware = ByparrMiddleware(byparr_provider=_FakeProvider())
    request = Request("https://example.com/", meta={"antibot_needed": True})

    result = middleware.process_request(request, spider=object())

    assert isinstance(result, Deferred)


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


def test_from_crawler_always_builds_a_patchright_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same reasoning as the Camoufox equivalent above: Patchright needs no
    external service/URL either, so it's always available."""
    monkeypatch.delenv("TITAN_BYPARR_URL", raising=False)

    class _FakeSettings:
        def get(self, name: str, default: object = None) -> object:
            return default

    class _FakeCrawler:
        settings = _FakeSettings()

    middleware = ByparrMiddleware.from_crawler(_FakeCrawler())

    assert middleware._providers["patchright"] is not None


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
