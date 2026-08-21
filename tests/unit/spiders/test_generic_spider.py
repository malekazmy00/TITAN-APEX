"""Unit tests for src/spiders/generic_spider.py.

``parse()`` is tested offline against a saved fixture page (the standard
way to unit-test a Scrapy spider: build a Response by hand and call the
callback directly, no network and no Scrapy engine involved).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from scrapy.crawler import Crawler
from scrapy.http import HtmlResponse, Request

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
