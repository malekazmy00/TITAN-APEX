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
