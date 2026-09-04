"""Unit tests for src/spiders/generic_spider.py.

``parse()`` is tested offline against a saved fixture page (the standard
way to unit-test a Scrapy spider: build a Response by hand and call the
callback directly, no network and no Scrapy engine involved).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from scrapy.crawler import Crawler
from scrapy.http import HtmlResponse, Request, TextResponse

from src.core.exceptions import ConfigError
from src.spiders.generic_spider import GenericSpider

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "targets"

CONFIG_YAML = """
name: quotes_toscrape
start_urls:
  - "https://quotes.toscrape.com/"
allowed_domains:
  - "quotes.toscrape.com"
rate_limit: 1.0
selectors:
  item: "div.quote"
  fields:
    text: "span.text::text"
    author: "small.author::text"
    tags: "div.tags a.tag::text"
next_page: "li.next a::attr(href)"
"""


@pytest.fixture
def config_path(tmp_path: Path) -> str:
    path = tmp_path / "quotes_toscrape.yaml"
    path.write_text(CONFIG_YAML, encoding="utf-8")
    return str(path)


def _fixture_response(url: str = "https://quotes.toscrape.com/") -> HtmlResponse:
    html = (FIXTURES_DIR / "quotes_toscrape_page1.html").read_text(encoding="utf-8")
    request = Request(url)
    return HtmlResponse(url=url, body=html.encode("utf-8"), request=request)


def test_spider_extracts_items_from_fixture_page(config_path: str) -> None:
    """Happy path: parse() extracts every quote item with all fields populated."""
    spider = GenericSpider(config_path=config_path)
    response = _fixture_response()

    results = list(spider.parse(response))
    items = [r for r in results if isinstance(r, dict)]

    assert len(items) == 10
    first = items[0]
    assert first["author"] == "Albert Einstein"
    assert "world as we have created it" in first["text"]
    assert isinstance(first["tags"], list)
    assert first["source_url"] == "https://quotes.toscrape.com/"


def test_parse_uses_live_dom_items_from_response_meta_when_present(config_path: str) -> None:
    """docs/REQUIREMENTS.md section 9 entry 12: when ByparrMiddleware
    already attached live_dom_items to request.meta (a real-browser
    provider's own live-DOM extraction), parse() must yield those directly
    -- never re-parse response.text, which would silently miss whatever
    was only reachable live (entry 11's confirmed Shadow DOM gap)."""
    spider = GenericSpider(config_path=config_path)
    request = Request(
        "https://quotes.toscrape.com/",
        meta={"live_dom_items": [{"author": "alice", "text": "hi"}]},
    )
    # A deliberately WRONG body -- if parse() ever fell back to parsing
    # this instead of using live_dom_items, the assertions below would
    # catch it (this fixture page's real content is completely different).
    response = HtmlResponse(
        url="https://quotes.toscrape.com/",
        body=b"<html><body>this should never be parsed</body></html>",
        request=request,
    )

    results = list(spider.parse(response))
    items = [r for r in results if isinstance(r, dict)]

    assert items == [
        {"source_url": "https://quotes.toscrape.com/", "author": "alice", "text": "hi"}
    ]


def test_parse_logs_and_yields_nothing_when_live_dom_items_is_an_empty_list(
    config_path: str,
) -> None:
    """Failure-adjacent case: a provider that genuinely performed live-DOM
    extraction but found zero matches is a real "no items" result (same
    warning as the parsed_html path's own empty-rows case), not silently
    different behavior."""
    spider = GenericSpider(config_path=config_path)
    request = Request("https://quotes.toscrape.com/", meta={"live_dom_items": []})
    response = HtmlResponse(
        url="https://quotes.toscrape.com/", body=b"<html></html>", request=request
    )

    results = list(spider.parse(response))

    assert results == []


def test_from_crawler_wires_download_delay_and_storage_pipeline(config_path: str) -> None:
    """from_crawler must apply the per-target DOWNLOAD_DELAY, concurrency, the storage
    pipeline, and the Phase 1+2 downloader middlewares on crawler.settings — a plain
    instance-level custom_settings assignment does *not* achieve this, since Scrapy
    reads custom_settings off the class before the instance (and its YAML config)
    exists."""
    crawler = Crawler(GenericSpider, settings={})

    spider = GenericSpider.from_crawler(crawler, config_path=config_path)

    assert spider.config.rate_limit == 1.0
    assert crawler.settings.getfloat("DOWNLOAD_DELAY") == 1.0
    assert crawler.settings.getint("CONCURRENT_REQUESTS_PER_DOMAIN") == 2
    assert crawler.settings.getdict("ITEM_PIPELINES") == {
        "src.spiders.pipelines.StorageBackendPipeline": 300
    }
    assert crawler.settings.getdict("DOWNLOADER_MIDDLEWARES") == {
        "src.middlewares.rate_limiter.RateLimiterMiddleware": 100,
        "src.middlewares.byparr_middleware.ByparrMiddleware": 520,
        "src.middlewares.playwright_middleware.PlaywrightMiddleware": 543,
        "src.middlewares.retry_backoff.RetryBackoffMiddleware": 550,
        "src.middlewares.circuit_breaker.CircuitBreakerMiddleware": 900,
    }


def test_start_requests_sets_playwright_meta_from_config(config_path: str) -> None:
    """render_js in the config must land on every start request's meta.
    (Legacy sync entry point, kept for Scrapy < 2.13.)"""
    spider = GenericSpider(config_path=config_path)

    requests = list(spider.start_requests())

    assert len(requests) == 1
    assert requests[0].meta["playwright"] is False  # default render_js is False


def test_start_requests_sets_playwright_meta_true_when_render_js_enabled(tmp_path: Path) -> None:
    config_file = tmp_path / "js_target.yaml"
    config_file.write_text(CONFIG_YAML + "\nrender_js: true\n", encoding="utf-8")
    spider = GenericSpider(config_path=str(config_file))

    requests = list(spider.start_requests())

    assert requests[0].meta["playwright"] is True


def _run_async_start(spider: GenericSpider) -> list[Request]:
    async def collect() -> list[Request]:
        return [request async for request in spider.start()]

    return asyncio.run(collect())


def test_start_sets_playwright_meta_from_config(config_path: str) -> None:
    """Regression test: Scrapy >= 2.13 calls start() (async), not start_requests().
    The base Spider.start() default silently ignores any start_requests() override
    and yields plain Request(url, dont_filter=True) with no custom meta at all —
    this must independently carry the same per-target playwright meta."""
    spider = GenericSpider(config_path=config_path)

    requests = _run_async_start(spider)

    assert len(requests) == 1
    assert requests[0].meta["playwright"] is False


def test_start_sets_playwright_meta_true_when_render_js_enabled(tmp_path: Path) -> None:
    config_file = tmp_path / "js_target.yaml"
    config_file.write_text(CONFIG_YAML + "\nrender_js: true\n", encoding="utf-8")
    spider = GenericSpider(config_path=str(config_file))

    requests = _run_async_start(spider)

    assert requests[0].meta["playwright"] is True


def test_start_sets_antibot_needed_meta_from_config(config_path: str) -> None:
    spider = GenericSpider(config_path=config_path)

    requests = _run_async_start(spider)

    assert requests[0].meta["antibot_needed"] is False


def test_start_sets_antibot_needed_meta_true_when_enabled(tmp_path: Path) -> None:
    config_file = tmp_path / "antibot_target.yaml"
    config_file.write_text(CONFIG_YAML + "\nantibot_needed: true\n", encoding="utf-8")
    spider = GenericSpider(config_path=str(config_file))

    requests = _run_async_start(spider)

    assert requests[0].meta["antibot_needed"] is True


def test_start_sets_render_wait_ms_and_click_selector_meta_to_none_by_default(
    config_path: str,
) -> None:
    """render_wait_ms/click_selector are opt-in -- every existing target (and any
    new one that doesn't need them) must see None for both, not a crash or a
    surprising default."""
    spider = GenericSpider(config_path=config_path)

    requests = _run_async_start(spider)

    assert requests[0].meta["render_wait_ms"] is None
    assert requests[0].meta["click_selector"] is None


def test_start_sets_render_wait_ms_and_click_selector_meta_from_config(
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "wait_and_click_target.yaml"
    config_file.write_text(
        CONFIG_YAML + '\nrender_wait_ms: 2500\nclick_selector: "button.load-more"\n',
        encoding="utf-8",
    )
    spider = GenericSpider(config_path=str(config_file))

    requests = _run_async_start(spider)

    assert requests[0].meta["render_wait_ms"] == 2500
    assert requests[0].meta["click_selector"] == "button.load-more"


def test_start_sets_antibot_provider_meta_to_byparr_by_default(config_path: str) -> None:
    spider = GenericSpider(config_path=config_path)

    requests = _run_async_start(spider)

    assert requests[0].meta["antibot_provider"] == "byparr"


def test_start_sets_antibot_provider_meta_from_config(tmp_path: Path) -> None:
    config_file = tmp_path / "camoufox_target.yaml"
    config_file.write_text(
        CONFIG_YAML + "\nantibot_needed: true\nantibot_provider: camoufox\n", encoding="utf-8"
    )
    spider = GenericSpider(config_path=str(config_file))

    requests = _run_async_start(spider)

    assert requests[0].meta["antibot_provider"] == "camoufox"


def test_start_sets_extraction_selectors_meta_to_none_by_default(config_path: str) -> None:
    """extraction_mode defaults to "parsed_html" -- every existing target
    (and any new one that doesn't opt into live_dom) must see None, not a
    crash or a surprising default (docs/REQUIREMENTS.md section 9 entry 12)."""
    spider = GenericSpider(config_path=config_path)

    requests = _run_async_start(spider)

    assert requests[0].meta["extraction_selectors"] is None


def test_start_sets_extraction_selectors_meta_from_config_when_live_dom(tmp_path: Path) -> None:
    config_file = tmp_path / "live_dom_target.yaml"
    config_file.write_text(
        CONFIG_YAML + "\nantibot_needed: true\nantibot_provider: camoufox\n"
        "extraction_mode: live_dom\n",
        encoding="utf-8",
    )
    spider = GenericSpider(config_path=str(config_file))

    requests = _run_async_start(spider)

    extraction_selectors = requests[0].meta["extraction_selectors"]
    assert extraction_selectors.item == "div.quote"
    assert extraction_selectors.fields == spider.config.selectors.fields


def test_parse_pagination_follow_carries_playwright_meta(tmp_path: Path) -> None:
    config_file = tmp_path / "js_target.yaml"
    config_file.write_text(CONFIG_YAML + "\nrender_js: true\n", encoding="utf-8")
    spider = GenericSpider(config_path=str(config_file))
    response = _fixture_response()

    results = list(spider.parse(response))
    follow_requests = [r for r in results if isinstance(r, Request)]

    assert len(follow_requests) == 1
    assert follow_requests[0].meta["playwright"] is True


def test_missing_config_path_raises_config_error() -> None:
    """Failure case 1: constructing the spider without a config raises loudly."""
    with pytest.raises(ConfigError, match="requires a 'config_path'"):
        GenericSpider(config_path=None)


def test_parse_on_page_with_no_matching_items_logs_and_yields_nothing(
    config_path: str,
) -> None:
    """Failure case 2: a page without matching selectors yields no items (no crash)."""
    spider = GenericSpider(config_path=config_path)
    request = Request("https://quotes.toscrape.com/empty")
    response = HtmlResponse(
        url="https://quotes.toscrape.com/empty",
        body=b"<html><body><p>nothing here</p></body></html>",
        request=request,
    )

    results = list(spider.parse(response))

    assert results == []


# --- selectors.item_group_size / positional extraction (docs/REQUIREMENTS.md
# section 9 entry 23, Known Limitation #5's real fix) -----------------------

POSITIONAL_CONFIG_YAML = """
name: spa_catalog
start_urls:
  - "http://localhost:8080/spa-catalog"
allowed_domains:
  - "localhost"
rate_limit: 1.0
selectors:
  item: "#catalog > *"
  item_group_size: 3
  fields:
    image_url: "0::attr(src)"
    title: "1::text"
    price: "2::text"
"""

# Deliberately no per-item wrapper and no stable class/attribute anywhere
# (a real CSS-in-JS/styled-components shape) -- the exact page
# extract_positional_html_items exists for; hashed-looking class names on
# purpose (opaque, not semantic) to prove extraction never depends on them.
POSITIONAL_HTML = b"""
<html><body>
  <main id="catalog">
    <img class="x1a1a1a1" src="/img/1.png" alt="Widget">
    <h3 class="x2b2b2b2">Widget</h3>
    <span class="x3c3c3c3">$9.99</span>
    <img class="x1a1a1a1" src="/img/2.png" alt="Gadget">
    <h3 class="x2b2b2b2">Gadget</h3>
    <span class="x3c3c3c3">$19.99</span>
  </main>
</body></html>
"""


@pytest.fixture
def positional_config_path(tmp_path: Path) -> str:
    path = tmp_path / "spa_catalog.yaml"
    path.write_text(POSITIONAL_CONFIG_YAML, encoding="utf-8")
    return str(path)


def test_parse_extracts_items_positionally_when_item_group_size_is_set(
    positional_config_path: str,
) -> None:
    """Happy path: a page with zero stable classes/attributes still
    yields correct, well-formed items -- position, not name, drives
    extraction."""
    spider = GenericSpider(config_path=positional_config_path)
    request = Request("http://localhost:8080/spa-catalog")
    response = HtmlResponse(
        url="http://localhost:8080/spa-catalog", body=POSITIONAL_HTML, request=request
    )

    results = list(spider.parse(response))
    items = [r for r in results if isinstance(r, dict)]

    assert items == [
        {
            "source_url": "http://localhost:8080/spa-catalog",
            "image_url": "/img/1.png",
            "title": "Widget",
            "price": "$9.99",
        },
        {
            "source_url": "http://localhost:8080/spa-catalog",
            "image_url": "/img/2.png",
            "title": "Gadget",
            "price": "$19.99",
        },
    ]


def test_parse_positional_extraction_on_empty_page_logs_and_yields_nothing(
    positional_config_path: str,
) -> None:
    """Failure case: no matching slots at all -- no crash, no items."""
    spider = GenericSpider(config_path=positional_config_path)
    request = Request("http://localhost:8080/spa-catalog")
    response = HtmlResponse(
        url="http://localhost:8080/spa-catalog",
        body=b"<html><body><main id='catalog'></main></body></html>",
        request=request,
    )

    results = list(spider.parse(response))

    assert results == []


# --- JSON/API parsing (docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md's
# JSON/API round) -------------------------------------------------------

JSON_CONFIG_YAML = """
name: mock_target_feed
start_urls:
  - "http://localhost:8080/api/feed"
allowed_domains:
  - "localhost"
rate_limit: 1.0
response_format: json
json_selectors:
  items_path: "edges"
  fields:
    post_id: "post.id"
    author: "post.author"
    text: "post.text"
    likes: "post.likes"
  next_cursor_path: "page_info.end_cursor"
  has_next_page_path: "page_info.has_next_page"
"""


@pytest.fixture
def json_config_path(tmp_path: Path) -> str:
    path = tmp_path / "mock_target_feed.yaml"
    path.write_text(JSON_CONFIG_YAML, encoding="utf-8")
    return str(path)


def _json_response(url: str, payload: object) -> TextResponse:
    request = Request(url)
    return TextResponse(
        url=url, body=json.dumps(payload).encode("utf-8"), request=request
    )


def test_parse_json_extracts_items_and_follows_pagination(json_config_path: str) -> None:
    """Happy path: dotted-path field extraction from a nested JSON API
    response, plus following the next page via the after= cursor --
    test-environment/mock-target's own /api/feed shape exactly."""
    spider = GenericSpider(config_path=json_config_path)
    payload = {
        "edges": [
            {"post": {"id": "p1", "author": "alice", "text": "hi", "likes": 3}},
            {"post": {"id": "p2", "author": "bob", "text": "yo", "likes": 5}},
        ],
        "page_info": {"end_cursor": "p2", "has_next_page": True},
    }
    response = _json_response("http://localhost:8080/api/feed", payload)

    results = list(spider.parse(response))
    items = [r for r in results if isinstance(r, dict)]
    follow_requests = [r for r in results if isinstance(r, Request)]

    assert len(items) == 2
    assert items[0] == {
        "source_url": "http://localhost:8080/api/feed",
        "post_id": "p1",
        "author": "alice",
        "text": "hi",
        "likes": 3,
    }
    assert len(follow_requests) == 1
    assert follow_requests[0].url == "http://localhost:8080/api/feed?after=p2"


def test_parse_json_stops_pagination_when_has_next_page_is_false(
    json_config_path: str,
) -> None:
    """Failure-adjacent case 1: the last page must not yield a follow-up request."""
    spider = GenericSpider(config_path=json_config_path)
    payload = {
        "edges": [{"post": {"id": "p1", "author": "alice", "text": "hi", "likes": 3}}],
        "page_info": {"end_cursor": "p1", "has_next_page": False},
    }
    response = _json_response("http://localhost:8080/api/feed", payload)

    results = list(spider.parse(response))
    follow_requests = [r for r in results if isinstance(r, Request)]

    assert follow_requests == []


def test_parse_json_invalid_json_logs_and_yields_nothing(json_config_path: str) -> None:
    """Failure case 3: a malformed JSON body (e.g. an upstream error page)
    must not crash the spider -- logged and skipped, no items."""
    spider = GenericSpider(config_path=json_config_path)
    request = Request("http://localhost:8080/api/feed")
    response = TextResponse(
        url="http://localhost:8080/api/feed", body=b"{not valid json", request=request
    )

    results = list(spider.parse(response))

    assert results == []


def test_parse_json_items_path_not_a_list_logs_and_yields_nothing(
    json_config_path: str,
) -> None:
    """Failure case 4: items_path resolving to a non-list (a schema surprise,
    e.g. an error object instead of the expected edges array) must not
    crash iterating over it as if it were one."""
    spider = GenericSpider(config_path=json_config_path)
    response = _json_response(
        "http://localhost:8080/api/feed", {"error": "rate_limited", "edges": "oops"}
    )

    results = list(spider.parse(response))

    assert results == []


def test_parse_json_missing_field_path_resolves_to_none(json_config_path: str) -> None:
    """Failure-adjacent case 5: a field whose dotted path is absent on a
    given item resolves to None, the same "field comes back empty" shape
    the CSS path already has -- not a KeyError."""
    spider = GenericSpider(config_path=json_config_path)
    payload = {
        "edges": [{"post": {"id": "p1", "author": "alice"}}],  # no text/likes
        "page_info": {"end_cursor": "p1", "has_next_page": False},
    }
    response = _json_response("http://localhost:8080/api/feed", payload)

    results = list(spider.parse(response))
    items = [r for r in results if isinstance(r, dict)]

    assert items[0]["text"] is None
    assert items[0]["likes"] is None


# --- login/session (docs/REQUIREMENTS.md section 9 entry 15, Known
# Limitation #1, activated ahead of Interstitials per explicit user
# request) --------------------------------------------------------------

LOGIN_CONFIG_YAML = (
    CONFIG_YAML
    + "\nantibot_needed: true\nantibot_provider: camoufox\n"
    "login:\n"
    "  login_url: http://localhost:8080/login\n"
    "  username: titan_test_user\n"
    "  password: titan_test_pass\n"
    "  username_field: '#username'\n"
    "  password_field: '#password'\n"
    "  submit_selector: '#login-submit'\n"
)


def test_start_sets_login_flow_meta_to_none_by_default(config_path: str) -> None:
    spider = GenericSpider(config_path=config_path)

    requests = _run_async_start(spider)

    assert requests[0].meta["login_flow"] is None


def test_start_sets_login_flow_meta_from_config(tmp_path: Path) -> None:
    config_file = tmp_path / "login_target.yaml"
    config_file.write_text(LOGIN_CONFIG_YAML, encoding="utf-8")
    spider = GenericSpider(config_path=str(config_file))

    requests = _run_async_start(spider)

    login_flow = requests[0].meta["login_flow"]
    assert login_flow.login_url == "http://localhost:8080/login"
    assert login_flow.username == "titan_test_user"
    assert login_flow.password == "titan_test_pass"
    assert login_flow.username_field == "#username"
    assert login_flow.password_field == "#password"
    assert login_flow.submit_selector == "#login-submit"
    assert login_flow.session_expiry_probe_url is None


def test_start_sets_handle_httpstatus_list_even_without_login_configured(config_path: str) -> None:
    """docs/REQUIREMENTS.md section 9 entry 15: set unconditionally, not
    just for a `login`-configured target -- a target that requires a
    session but has no login configured at all must still surface a real
    401/403 (the user's own explicit requirement), not have Scrapy's own
    HttpErrorMiddleware silently drop it before parse() ever sees it.
    Harmless for every other target too (this module's own comment on
    why: Anubis's challenge/deny pages always return 200 by design)."""
    spider = GenericSpider(config_path=config_path)

    requests = _run_async_start(spider)

    assert requests[0].meta["handle_httpstatus_list"] == [401, 403]


def test_start_sets_handle_httpstatus_list_when_login_configured_too(tmp_path: Path) -> None:
    """Same value, same reasoning, for a `login`-configured target --
    included for parity/regression coverage, not because the behavior
    actually differs from the unconditional case above."""
    config_file = tmp_path / "login_target.yaml"
    config_file.write_text(LOGIN_CONFIG_YAML, encoding="utf-8")
    spider = GenericSpider(config_path=str(config_file))

    requests = _run_async_start(spider)

    assert requests[0].meta["handle_httpstatus_list"] == [401, 403]


def test_parse_logs_and_yields_nothing_on_401(config_path: str) -> None:
    """Happy path for the detection logic itself: a real 401 (no valid
    session, a failed login, or one that expired mid-crawl) is logged
    clearly and explicitly, not a silent drop and not a crash."""
    spider = GenericSpider(config_path=config_path)
    request = Request("https://quotes.toscrape.com/")
    response = HtmlResponse(
        url="https://quotes.toscrape.com/",
        body=b'{"error": "unauthorized"}',
        request=request,
        status=401,
    )

    results = list(spider.parse(response))

    assert results == []


def test_parse_logs_and_yields_nothing_on_403(config_path: str) -> None:
    """Failure-adjacent case: same handling for 403 (e.g. an invalid/
    replayed CSRF token) as for 401."""
    spider = GenericSpider(config_path=config_path)
    request = Request("https://quotes.toscrape.com/")
    response = HtmlResponse(
        url="https://quotes.toscrape.com/",
        body=b'{"error": "invalid_csrf_token"}',
        request=request,
        status=403,
    )

    results = list(spider.parse(response))

    assert results == []


# --- Referer path consistency + session warm-up, Levels 1/2 (docs/
# REQUIREMENTS.md section 9 entry 21, Step 1) ---------------------------

WARM_SESSION_CONFIG_YAML = CONFIG_YAML + (
    "\nwarm_session_urls:\n"
    '  - "https://quotes.toscrape.com/warmup-home"\n'
    '  - "https://quotes.toscrape.com/warmup-category"\n'
)


@pytest.fixture
def warm_session_config_path(tmp_path: Path) -> str:
    path = tmp_path / "warm_session_target.yaml"
    path.write_text(WARM_SESSION_CONFIG_YAML, encoding="utf-8")
    return str(path)


def test_start_requests_go_direct_to_start_urls_when_no_warm_session_configured(
    config_path: str,
) -> None:
    """Regression sentinel: warm_session_urls defaults to an empty list --
    every existing config (this fixture has no such field at all) must
    keep the exact prior direct-to-start_urls behavior, unchanged."""
    spider = GenericSpider(config_path=config_path)

    requests = _run_async_start(spider)

    assert len(requests) == 1
    assert requests[0].url == "https://quotes.toscrape.com/"
    assert requests[0].callback == spider.parse


def test_start_requests_goes_to_first_warm_session_url_when_configured(
    warm_session_config_path: str,
) -> None:
    """Happy path: a configured warm_session_urls means the very first
    request goes there, not to start_urls -- the real target is only
    reached after the full warm-up chain (see
    test_parse_warm_session_step_* below)."""
    spider = GenericSpider(config_path=warm_session_config_path)

    requests = _run_async_start(spider)

    assert len(requests) == 1
    assert requests[0].url == "https://quotes.toscrape.com/warmup-home"
    assert requests[0].callback == spider._parse_warm_session_step
    assert requests[0].meta["warm_session_index"] == 0


def test_start_requests_go_direct_to_start_urls_when_antibot_needed_even_with_warm_session_urls(
    tmp_path: Path,
) -> None:
    """docs/REQUIREMENTS.md section 9 entry 21, Step 2: an antibot-
    protected target must NOT build the Step 1 Scrapy-level hop chain
    (each hop would trigger its own independent, disconnected
    provider.solve() call) -- warm_session_urls instead travels in the
    real target request's own meta, for the provider itself to walk
    inside one continuous browser session."""
    config_file = tmp_path / "antibot_warm_session_target.yaml"
    config_file.write_text(
        CONFIG_YAML + "\nantibot_needed: true\nantibot_provider: camoufox\n"
        'warm_session_urls:\n  - "https://quotes.toscrape.com/warmup-home"\n',
        encoding="utf-8",
    )
    spider = GenericSpider(config_path=str(config_file))

    requests = _run_async_start(spider)

    assert len(requests) == 1
    assert requests[0].url == "https://quotes.toscrape.com/"
    assert requests[0].callback == spider.parse
    assert requests[0].meta["warm_session_urls"] == ["https://quotes.toscrape.com/warmup-home"]


def test_request_meta_includes_warm_session_urls_and_use_accumulated_profile(
    config_path: str,
) -> None:
    """Both new keys reach request.meta unconditionally (harmless no-ops
    for a target that doesn't use them -- see _request_meta's own
    comment) -- this is what byparr_middleware.py's own process_request
    actually reads."""
    spider = GenericSpider(config_path=config_path)

    requests = _run_async_start(spider)

    assert requests[0].meta["warm_session_urls"] == []
    assert requests[0].meta["use_accumulated_profile"] is False


def test_request_meta_defaults_user_agent_override_to_none(config_path: str) -> None:
    """docs/REQUIREMENTS.md section 9 entry 24/27: reaches
    request.meta unconditionally (same "harmless no-op" shape as
    warm_session_urls/use_accumulated_profile above) -- every existing
    config (none of which sets user_agent_override) must keep getting
    None here, exactly as before this field existed."""
    spider = GenericSpider(config_path=config_path)

    requests = _run_async_start(spider)

    assert requests[0].meta["user_agent_override"] is None


def test_request_meta_includes_a_configured_user_agent_override(tmp_path: Path) -> None:
    """Happy path: a target that sets user_agent_override gets it verbatim
    in request.meta -- this is what byparr_middleware.py's own
    process_request actually reads and forwards to the provider."""
    config_file = tmp_path / "ua_override_target.yaml"
    config_file.write_text(
        CONFIG_YAML + '\nuser_agent_override: "Mozilla/5.0 (custom-test-ua)"\n',
        encoding="utf-8",
    )
    spider = GenericSpider(config_path=str(config_file))

    requests = _run_async_start(spider)

    assert requests[0].meta["user_agent_override"] == "Mozilla/5.0 (custom-test-ua)"


def test_request_meta_defaults_block_webrtc_to_false(config_path: str) -> None:
    """docs/PHASE_2_BACKLOG.md item 5: same "harmless no-op" shape as
    user_agent_override above -- every existing config (none of which
    sets block_webrtc) must keep getting False."""
    spider = GenericSpider(config_path=config_path)

    requests = _run_async_start(spider)

    assert requests[0].meta["block_webrtc"] is False


def test_request_meta_includes_a_configured_block_webrtc(tmp_path: Path) -> None:
    """Happy path: a target that sets block_webrtc gets it verbatim in
    request.meta -- this is what byparr_middleware.py's own
    process_request actually reads and forwards to the provider."""
    config_file = tmp_path / "block_webrtc_target.yaml"
    config_file.write_text(CONFIG_YAML + "\nblock_webrtc: true\n", encoding="utf-8")
    spider = GenericSpider(config_path=str(config_file))

    requests = _run_async_start(spider)

    assert requests[0].meta["block_webrtc"] is True


def test_request_meta_defaults_strategy_backoff_multiplier_to_none(config_path: str) -> None:
    """docs/REQUIREMENTS.md section 9 entry 30: same "harmless no-op"
    shape as user_agent_override above -- every existing config (none of
    which sets strategy_backoff_multiplier) must keep getting None."""
    spider = GenericSpider(config_path=config_path)

    requests = _run_async_start(spider)

    assert requests[0].meta["strategy_backoff_multiplier"] is None


def test_request_meta_includes_a_configured_strategy_backoff_multiplier(tmp_path: Path) -> None:
    config_file = tmp_path / "backoff_multiplier_target.yaml"
    config_file.write_text(
        CONFIG_YAML + "\nstrategy_backoff_multiplier: 2.5\n", encoding="utf-8"
    )
    spider = GenericSpider(config_path=str(config_file))

    requests = _run_async_start(spider)

    assert requests[0].meta["strategy_backoff_multiplier"] == 2.5


def test_parse_warm_session_step_follows_to_the_next_warm_url(
    warm_session_config_path: str,
) -> None:
    """Middle of the chain: two warm_session_urls configured, so finishing
    step 0 must follow to step 1 -- not to start_urls yet."""
    spider = GenericSpider(config_path=warm_session_config_path)
    request = Request(
        "https://quotes.toscrape.com/warmup-home", meta={"warm_session_index": 0}
    )
    response = HtmlResponse(
        url="https://quotes.toscrape.com/warmup-home", body=b"<html></html>", request=request
    )

    results = list(spider._parse_warm_session_step(response))

    assert len(results) == 1
    assert results[0].url == "https://quotes.toscrape.com/warmup-category"
    assert results[0].callback == spider._parse_warm_session_step
    assert results[0].meta["warm_session_index"] == 1


def test_parse_warm_session_step_follows_to_every_start_url_after_the_last_hop(
    warm_session_config_path: str,
) -> None:
    """End of the chain: the last configured warm_session_urls hop must
    follow to every real start_urls target, with the real per-target meta
    (parse callback, antibot/render settings, etc.) -- not the bare
    warm_session_index-only meta the warm-up hops themselves carry."""
    spider = GenericSpider(config_path=warm_session_config_path)
    request = Request(
        "https://quotes.toscrape.com/warmup-category", meta={"warm_session_index": 1}
    )
    response = HtmlResponse(
        url="https://quotes.toscrape.com/warmup-category",
        body=b"<html></html>",
        request=request,
    )

    results = list(spider._parse_warm_session_step(response))

    assert len(results) == 1
    assert results[0].url == "https://quotes.toscrape.com/"
    assert results[0].callback == spider.parse
    assert results[0].meta["playwright"] is False
    assert "warm_session_index" not in results[0].meta


def test_parse_warm_session_step_supports_multiple_real_start_urls(tmp_path: Path) -> None:
    """A warm-up chain fans out to *every* start_urls target, not just the
    first -- each with the last warm-up page as its own real Referer
    (Scrapy's own already-enabled RefererMiddleware computes this from
    the response each request is yielded alongside; see
    _parse_warm_session_step's own docstring)."""
    config_file = tmp_path / "multi_target.yaml"
    config_file.write_text(
        CONFIG_YAML.replace(
            'start_urls:\n  - "https://quotes.toscrape.com/"',
            'start_urls:\n  - "https://quotes.toscrape.com/a"\n'
            '  - "https://quotes.toscrape.com/b"',
        )
        + '\nwarm_session_urls:\n  - "https://quotes.toscrape.com/warmup-home"\n',
        encoding="utf-8",
    )
    spider = GenericSpider(config_path=str(config_file))
    request = Request(
        "https://quotes.toscrape.com/warmup-home", meta={"warm_session_index": 0}
    )
    response = HtmlResponse(
        url="https://quotes.toscrape.com/warmup-home", body=b"<html></html>", request=request
    )

    results = list(spider._parse_warm_session_step(response))

    assert [r.url for r in results] == [
        "https://quotes.toscrape.com/a",
        "https://quotes.toscrape.com/b",
    ]
    assert all(r.callback == spider.parse for r in results)


def test_parse_does_not_treat_a_normal_200_as_rejected(config_path: str) -> None:
    """Sanity/regression check: the new 401/403 branch must not somehow
    swallow an ordinary successful response too."""
    spider = GenericSpider(config_path=config_path)
    response = _fixture_response()

    results = list(spider.parse(response))
    items = [r for r in results if isinstance(r, dict)]

    assert len(items) == 10
