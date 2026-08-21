"""A single Scrapy spider driven entirely by a YAML config file.

Usage::

    scrapy runspider src/spiders/generic_spider.py \\
        -a config_path=src/spiders/configs/quotes_toscrape.yaml

No target-specific Python code is needed: point ``config_path`` at a new
``configs/*.yaml`` file to scrape a different target.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import scrapy
from scrapy.http import Response

from src.core.exceptions import ConfigError
from src.logging_config import get_logger
from src.spiders.spider_config import load_spider_config


def _resolve_json_path(data: Any, path: str) -> Any:
    """Drill into nested dicts via a dotted path (e.g. ``"post.author"``).

    Returns ``None`` for any missing key or non-dict intermediate value --
    the same "field just comes back empty" behavior the CSS path already
    has (``response.css(...).getall()`` returning nothing), rather than
    raising on a merely-absent optional field.
    """
    current = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _build_next_json_url(url: str, cursor: str) -> str:
    """Same URL with its ``after`` query param set to ``cursor`` --
    matches test-environment/mock-target's own ``/api/feed?after=<cursor>``
    paging contract exactly."""
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query))
    query["after"] = cursor
    return urlunparse(parsed._replace(query=urlencode(query)))


class GenericSpider(scrapy.Spider):
    """Config-driven spider: target, selectors and rate limit all come from YAML."""

    name = "generic"

    def __init__(self, config_path: str | None = None, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if not config_path:
            raise ConfigError(
                "generic_spider requires a 'config_path' argument, "
                "e.g. -a config_path=src/spiders/configs/quotes_toscrape.yaml"
            )
        self.config = load_spider_config(config_path)
        self.start_urls = list(self.config.start_urls)
        self.allowed_domains = list(self.config.allowed_domains)
        self.json_logger = get_logger(f"spiders.{self.config.name}")

    @classmethod
    def from_crawler(cls, crawler: Any, *args: Any, **kwargs: Any) -> GenericSpider:
        # NOTE: a plain `self.custom_settings = {...}` assigned in __init__
        # has no effect — Scrapy reads `custom_settings` off the *class*
        # (via Spider.update_settings) before the spider instance (and
        # therefore its YAML config) even exists. `crawler.settings` is
        # still mutable at this point (from_crawler runs before the engine
        # starts and freezes it), so this is where per-target settings
        # that depend on config_path must be applied instead.
        spider = super().from_crawler(crawler, *args, **kwargs)
        crawler.settings.set("DOWNLOAD_DELAY", spider.config.rate_limit, priority="spider")
        crawler.settings.set(
            "CONCURRENT_REQUESTS_PER_DOMAIN", spider.config.max_concurrency, priority="spider"
        )
        crawler.settings.set(
            "ITEM_PIPELINES",
            {"src.spiders.pipelines.StorageBackendPipeline": 300},
            priority="spider",
        )
        crawler.settings.set(
            "DOWNLOADER_MIDDLEWARES",
            {
                # Lower number = closer to the Engine = checked/seen first
                # for process_request; higher = closer to the Downloader =
                # seen first for process_response/process_exception. See
                # each middleware's module docstring for why this order
                # matters (docs/REQUIREMENTS.md, section 2).
                "src.middlewares.byparr_middleware.ByparrMiddleware": 520,
                "src.middlewares.playwright_middleware.PlaywrightMiddleware": 543,
                "src.middlewares.retry_backoff.RetryBackoffMiddleware": 550,
                "src.middlewares.circuit_breaker.CircuitBreakerMiddleware": 900,
            },
            priority="spider",
        )
        return spider

    def _request_meta(self) -> dict[str, Any]:
        return {
            "playwright": self.config.render_js,
            "antibot_needed": self.config.antibot_needed,
            "antibot_provider": self.config.antibot_provider,
            "render_wait_ms": self.config.render_wait_ms,
            "click_selector": self.config.click_selector,
        }

    def _build_start_requests(self) -> Iterator[scrapy.Request]:
        for url in self.start_urls:
            yield scrapy.Request(url, callback=self.parse, meta=self._request_meta())

    async def start(self) -> AsyncIterator[scrapy.Request]:
        # Scrapy >= 2.13 calls this instead of start_requests() — see
        # https://docs.scrapy.org/en/latest/topics/request-response.html#start-requests.
        # A request built via the (still-supported) synchronous
        # start_requests() override below does NOT reach here on its own:
        # the base Spider.start() implementation Scrapy actually calls
        # ignores any override of start_requests() and yields plain
        # Request(url, dont_filter=True) instead, silently dropping our
        # per-target `meta`. Both methods are defined so this spider works
        # correctly on Scrapy versions before and after 2.13.
        for request in self._build_start_requests():
            yield request

    def start_requests(self) -> Iterator[scrapy.Request]:
        # Kept for Scrapy < 2.13, which calls this instead of start().
        yield from self._build_start_requests()

    def parse(self, response: Response, **kwargs: Any) -> Iterator[dict[str, Any] | scrapy.Request]:
        if self.config.response_format == "json":
            yield from self._parse_json(response)
            return
        yield from self._parse_html(response)

    def _parse_html(
        self, response: Response
    ) -> Iterator[dict[str, Any] | scrapy.Request]:
        selectors = self.config.selectors
        assert selectors is not None  # SpiderConfig guarantees this for response_format="html"
        rows = response.css(selectors.item)

        if not rows:
            self.json_logger.warning(
                "generic_spider.no_items_found",
                extra={"url": response.url, "item_selector": selectors.item},
            )

        for row in rows:
            item: dict[str, Any] = {"source_url": response.url}
            for field_name, css_expr in selectors.fields.items():
                values = row.css(css_expr).getall()
                if len(values) == 0:
                    item[field_name] = None
                elif len(values) == 1:
                    item[field_name] = values[0]
                else:
                    item[field_name] = values
            yield item

        if self.config.next_page:
            next_href = response.css(self.config.next_page).get()
            if next_href:
                yield response.follow(next_href, callback=self.parse, meta=self._request_meta())

    def _parse_json(
        self, response: Response
    ) -> Iterator[dict[str, Any] | scrapy.Request]:
        """Parses a JSON API response per ``self.config.json_selectors`` --
        docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md's JSON/API round,
        test-environment/mock-target's own ``/api/feed`` being the first
        real target for it (docs/REQUIREMENTS.md, "Antibot Provider
        Comparison" section's neighbor writeup has the full history).
        """
        json_selectors = self.config.json_selectors
        assert json_selectors is not None  # SpiderConfig guarantees this for response_format="json"

        try:
            # .json() lives on scrapy's TextResponse, not the base Response
            # this method is typed with (matching parse()'s own callback
            # signature) -- a JSON API response is always actually a
            # TextResponse at runtime, the same loose-but-correct typing
            # gap _parse_html's .css() calls already have (Response's own
            # stub doesn't carry it either, mypy just doesn't flag those).
            data = response.json()  # type: ignore[attr-defined]
        except ValueError:
            self.json_logger.warning(
                "generic_spider.invalid_json", extra={"url": response.url}
            )
            return

        items = _resolve_json_path(data, json_selectors.items_path)
        if not isinstance(items, list):
            self.json_logger.warning(
                "generic_spider.json_items_not_a_list",
                extra={"url": response.url, "items_path": json_selectors.items_path},
            )
            items = []

        if not items:
            self.json_logger.warning(
                "generic_spider.no_items_found",
                extra={"url": response.url, "item_selector": json_selectors.items_path},
            )

        for raw_item in items:
            item: dict[str, Any] = {"source_url": response.url}
            for field_name, path in json_selectors.fields.items():
                item[field_name] = _resolve_json_path(raw_item, path)
            yield item

        if json_selectors.next_cursor_path and json_selectors.has_next_page_path:
            has_next = _resolve_json_path(data, json_selectors.has_next_page_path)
            cursor = _resolve_json_path(data, json_selectors.next_cursor_path)
            if has_next and cursor:
                next_url = _build_next_json_url(response.url, cursor)
                yield scrapy.Request(next_url, callback=self.parse, meta=self._request_meta())
