"""Unit tests for src/spiders/generic_spider.py.

``parse()`` is tested offline against a saved fixture page (the standard
way to unit-test a Scrapy spider: build a Response by hand and call the
callback directly, no network and no Scrapy engine involved).
"""

from __future__ import annotations

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
    """from_crawler must apply the per-target DOWNLOAD_DELAY and enable the storage
    pipeline on crawler.settings — a plain instance-level custom_settings assignment
    does *not* achieve this, since Scrapy reads custom_settings off the class before
    the instance (and its YAML config) exists."""
    crawler = Crawler(GenericSpider, settings={})

    spider = GenericSpider.from_crawler(crawler, config_path=config_path)

    assert spider.config.rate_limit == 1.0
    assert crawler.settings.getfloat("DOWNLOAD_DELAY") == 1.0
    assert crawler.settings.getdict("ITEM_PIPELINES") == {
        "src.spiders.pipelines.StorageBackendPipeline": 300
    }


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
