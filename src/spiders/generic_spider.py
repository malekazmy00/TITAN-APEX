"""A single Scrapy spider driven entirely by a YAML config file.

Usage::

    scrapy runspider src/spiders/generic_spider.py \\
        -a config_path=src/spiders/configs/quotes_toscrape.yaml

No target-specific Python code is needed: point ``config_path`` at a new
``configs/*.yaml`` file to scrape a different target.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import scrapy
from scrapy.http import Response

from src.core.exceptions import ConfigError
from src.logging_config import get_logger
from src.spiders.spider_config import load_spider_config


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
            "ITEM_PIPELINES",
            {"src.spiders.pipelines.StorageBackendPipeline": 300},
            priority="spider",
        )
        return spider

    def parse(self, response: Response, **kwargs: Any) -> Iterator[dict[str, Any] | scrapy.Request]:
        selectors = self.config.selectors
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
                yield response.follow(next_href, callback=self.parse)
