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
from src.core.interfaces.antibot_provider import LiveDomSelectors, LoginFlow
from src.logging_config import get_logger
from src.providers.antibot.parsed_html import (
    extract_parsed_html_items,
    extract_positional_html_items,
)
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
                #
                # RateLimiterMiddleware (docs/REQUIREMENTS.md section 9
                # entry 22, Phase 2 بند 7): only has process_request (it
                # never looks at a response/exception), and belongs first
                # in that order deliberately -- it is a cheap, local check
                # that should reject an over-quota/too-regular request
                # before any of byparr/playwright's real, expensive
                # browser-driving work ever starts for it.
                "src.middlewares.rate_limiter.RateLimiterMiddleware": 100,
                "src.middlewares.byparr_middleware.ByparrMiddleware": 520,
                "src.middlewares.playwright_middleware.PlaywrightMiddleware": 543,
                "src.middlewares.retry_backoff.RetryBackoffMiddleware": 550,
                "src.middlewares.circuit_breaker.CircuitBreakerMiddleware": 900,
            },
            priority="spider",
        )
        return spider

    def _request_meta(self) -> dict[str, Any]:
        extraction_selectors: LiveDomSelectors | None = None
        if self.config.extraction_mode == "live_dom":
            # SpiderConfig's own validator guarantees `selectors` is set
            # whenever extraction_mode is "live_dom" (it requires
            # response_format "html", which itself requires `selectors`).
            assert self.config.selectors is not None
            extraction_selectors = LiveDomSelectors(
                item=self.config.selectors.item, fields=self.config.selectors.fields
            )
        meta: dict[str, Any] = {
            "playwright": self.config.render_js,
            "antibot_needed": self.config.antibot_needed,
            "antibot_provider": self.config.antibot_provider,
            "render_wait_ms": self.config.render_wait_ms,
            "click_selector": self.config.click_selector,
            "extraction_selectors": extraction_selectors,
            "progressive_extraction": self.config.progressive_extraction,
            "login_flow": None,
            # docs/REQUIREMENTS.md section 9 entry 21, Step 2: harmless
            # to always include -- ByparrMiddleware only ever reads this
            # meta at all when antibot_needed is also True (its own
            # process_request's first check), so for a plain
            # (antibot_needed: False) target this is simply never
            # looked at, the same "no-op for everything that doesn't use
            # it" shape every other optional meta key here already has.
            "warm_session_urls": self.config.warm_session_urls,
            "use_accumulated_profile": self.config.use_accumulated_profile,
            # docs/REQUIREMENTS.md section 9 entry 24/27: same "harmless
            # to always include" shape as warm_session_urls above --
            # ByparrMiddleware only reads this when antibot_needed is
            # also True, so it's a genuine no-op for every plain target.
            "user_agent_override": self.config.user_agent_override,
            # docs/REQUIREMENTS.md section 9 entry 15: a real, discovered
            # prerequisite gap, not incidental -- Scrapy's own
            # HttpErrorMiddleware (spider middleware, enabled by default)
            # silently drops any non-2xx response before it ever reaches
            # parse() at all, unless a request explicitly opts in via
            # handle_httpstatus_list. Set unconditionally (not just for a
            # `login`-configured target): a target that requires a
            # session but has *no* login configured at all must still
            # log a real, explicit 401/403 (the user's own explicit
            # requirement), not have it silently vanish -- and this is
            # harmless for every other target too, since Anubis's own
            # challenge/deny pages always return 200 by design (this
            # stack's own botPolicy.yaml, `status_codes: CHALLENGE: 200,
            # DENY: 200`), so nothing here ever intersects with an
            # antibot rejection.
            "handle_httpstatus_list": [401, 403],
        }
        if self.config.login is not None:
            login_flow = LoginFlow(
                login_url=self.config.login.login_url,
                username=self.config.login.username,
                password=self.config.login.password,
                username_field=self.config.login.username_field,
                password_field=self.config.login.password_field,
                submit_selector=self.config.login.submit_selector,
                session_expiry_probe_url=self.config.login.session_expiry_probe_url,
            )
            meta["login_flow"] = login_flow
        return meta

    def _build_start_requests(self) -> Iterator[scrapy.Request]:
        # docs/REQUIREMENTS.md section 9 entry 21: a non-empty
        # `warm_session_urls` changes the very first request(s) built --
        # exactly *how* depends on `antibot_needed`, a real, confirmed
        # architectural split (entry 21 Step 2's own investigation, not
        # a guess): every `provider.solve()` call
        # (CamoufoxProvider/PatchrightProvider) launches its own
        # completely independent browser with zero cookie/Referer
        # continuity from any other Scrapy request -- confirmed by
        # reading byparr_middleware.py/camoufox_provider.py directly.
        # So a Step 1-style *Scrapy-level* hop chain (multiple separate
        # requests, each independently reaching ByparrMiddleware) would
        # never actually connect the warm-up to the real target at all
        # for an antibot-protected one: each hop would trigger its own
        # unrelated solve(). Step 2's real fix instead skips the
        # Scrapy-level chain entirely and lets the provider itself walk
        # `warm_session_urls` inside one continuous browser session
        # (`_request_meta()`'s own `warm_session_urls` key, read by
        # `camoufox_provider.py`/`patchright_provider.py`'s own
        # `_default_*_solve` functions). For a plain (`antibot_needed:
        # False`) target, Scrapy's own request/response cycle *is* the
        # real navigation -- Step 1's original chain (real Referer/
        # Cookie middleware doing genuine work) stays exactly as it was.
        if self.config.warm_session_urls and not self.config.antibot_needed:
            yield scrapy.Request(
                self.config.warm_session_urls[0],
                callback=self._parse_warm_session_step,
                meta={"warm_session_index": 0},
            )
            return
        for url in self.start_urls:
            yield scrapy.Request(url, callback=self.parse, meta=self._request_meta())

    def _parse_warm_session_step(
        self, response: Response, **kwargs: Any
    ) -> Iterator[scrapy.Request]:
        """docs/REQUIREMENTS.md section 9 entry 21, Step 1 (Referer path
        consistency + session warm-up): walks ``config.warm_session_urls``
        one real hop at a time via :meth:`~scrapy.http.Response.follow`
        -- never a loop of independent, unrelated ``scrapy.Request``
        calls -- specifically so Scrapy's own ``RefererMiddleware``/
        ``CookiesMiddleware`` (both already enabled, confirmed by
        direct code inspection: neither this spider nor its own
        ``from_crawler`` ever touches ``SPIDER_MIDDLEWARES``/the cookie
        middleware) build a real, connected Referer chain and accumulate
        real session cookies across every hop -- exactly the "visit the
        homepage/category page(s) first" technique this entry's own
        sources document (Scrapfly/webautomation.io), not a header
        forged after the fact. Confirmed directly against Scrapy's own
        source (``spidermiddlewares/base.py``'s ``process_spider_output``,
        ``spidermiddlewares/referer.py``'s ``get_processed_request``):
        the Referer set on any request this method yields is computed
        from *this* callback's own ``response`` -- i.e. the warm-up page
        actually being processed right now -- automatically, with no
        extra code needed here beyond building the right request chain.

        Once every ``warm_session_urls`` hop is done, follows to every
        real ``start_urls`` target with the *last* warm-up page as their
        genuine Referer -- the actual point of this whole mechanism:
        the real target is reached having already "browsed" a real,
        connected path, not cold.

        This callback itself never yields items -- ``warm_session_urls``
        pages are pure waypoints, not something this crawl is configured
        to extract from (they typically have no ``selectors`` at all
        matching what this target's real content looks like).
        """
        index = response.meta["warm_session_index"] + 1
        if index < len(self.config.warm_session_urls):
            yield response.follow(
                self.config.warm_session_urls[index],
                callback=self._parse_warm_session_step,
                meta={"warm_session_index": index},
            )
            return
        for url in self.start_urls:
            yield response.follow(url, callback=self.parse, meta=self._request_meta())

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

        # docs/REQUIREMENTS.md section 9 entry 15: a real 401/403 reaches
        # here at all because of _request_meta's own unconditional
        # handle_httpstatus_list opt-in -- covers every real cause the
        # user's own requirement calls out (no valid session configured
        # at all, no login attempted, a failed login, or a session that
        # expired mid-crawl): logged clearly and explicitly, not a
        # silent drop and not a crash. Provider-level logs
        # (camoufox_provider.login_failed / .session_expired_mid_crawl)
        # carry the finer-grained "which of these happened" detail when
        # login was actually attempted this call; this is the
        # crawl-level safety net either way.
        if response.status in (401, 403):
            self.json_logger.warning(
                "generic_spider.protected_target_rejected",
                extra={"url": response.url, "status": response.status},
            )
            return

        # docs/REQUIREMENTS.md section 9 entry 14: when the provider
        # captured multiple HTML snapshots during progressive scrolling
        # (extraction_mode: "parsed_html" + progressive_extraction: true
        # -- ByparrMiddleware attaches them to request.meta the same way
        # live_dom_items reaches here below), each snapshot is parsed with
        # this same `selectors` block and merged, deduplicated by
        # `post_id`, across *all* of them -- not just the final one. A
        # single final read (what happens without progressive_extraction)
        # can only ever reflect whatever's in the DOM at that one moment,
        # missing anything a virtualized list (entry 13's confirmed gap)
        # already evicted by then.
        html_snapshots = response.meta.get("html_snapshots")
        if html_snapshots is not None:
            merged: dict[str, dict[str, Any]] = {}
            for snapshot in html_snapshots:
                for raw_item in extract_parsed_html_items(
                    snapshot, selectors.item, selectors.fields
                ):
                    post_id = raw_item.get("post_id")
                    if post_id is not None and post_id not in merged:
                        merged[post_id] = raw_item
            if not merged:
                self.json_logger.warning(
                    "generic_spider.no_items_found",
                    extra={"url": response.url, "item_selector": selectors.item},
                )
            for raw_item in merged.values():
                yield {"source_url": response.url, **raw_item}
        # docs/REQUIREMENTS.md section 9 entry 12: when a real, live
        # browser page already extracted items directly (extraction_mode:
        # "live_dom" -- ByparrMiddleware attaches them to request.meta,
        # visible here via response.meta's passthrough to the same
        # request object), those items ARE the real result -- re-parsing
        # `response.text` here would silently miss whatever was only ever
        # reachable live (a Shadow DOM's content, entry 11's confirmed
        # gap), since it was never in that serialized string to begin
        # with, and would also just be redundant work for everything else.
        elif (live_dom_items := response.meta.get("live_dom_items")) is not None:
            if not live_dom_items:
                self.json_logger.warning(
                    "generic_spider.no_items_found",
                    extra={"url": response.url, "item_selector": selectors.item},
                )
            for raw_item in live_dom_items:
                yield {"source_url": response.url, **raw_item}
        # docs/REQUIREMENTS.md section 9 entry 23: Known Limitation #5's
        # real fix -- a target with no stable class/attribute anywhere
        # (CSS-in-JS's hashed, build-time-generated class names) reaches
        # here instead of the plain response.css(selectors.item) path
        # below, which structurally cannot express "select this item's
        # container" when no such stable selector exists at all.
        elif selectors.item_group_size is not None:
            positional_items = extract_positional_html_items(
                response.text, selectors.item, selectors.item_group_size, selectors.fields
            )
            if not positional_items:
                self.json_logger.warning(
                    "generic_spider.no_items_found",
                    extra={"url": response.url, "item_selector": selectors.item},
                )
            for raw_item in positional_items:
                yield {"source_url": response.url, **raw_item}
        else:
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
            # body_snippet: real diagnostic evidence, not a guess -- e.g.
            # this is exactly what caught a real browser (Camoufox drives
            # Firefox, which has its own built-in JSON viewer) rendering
            # a raw JSON response as something response.json()'s plain
            # json.loads(response.text) can't parse
            # (docs/REQUIREMENTS.md section 9 entry 9).
            self.json_logger.warning(
                "generic_spider.invalid_json",
                extra={"url": response.url, "body_snippet": response.text[:300]},
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
