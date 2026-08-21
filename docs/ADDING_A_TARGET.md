# Adding a new scrape target

Adding a new target is a **config change, not a code change**. Do not write
a new spider class.

1. Create `src/spiders/configs/<target_name>.yaml`:

   ```yaml
   name: <target_name>
   start_urls:
     - "https://example.com/"
   allowed_domains:
     - "example.com"
   rate_limit: 1.0          # seconds between requests (DOWNLOAD_DELAY)
   max_concurrency: 2       # optional; CONCURRENT_REQUESTS_PER_DOMAIN, default 2
   render_js: false         # optional; true routes through PlaywrightMiddleware
   antibot_needed: false    # optional; true routes through ByparrMiddleware first
   selectors:
     item: "div.result"           # CSS selector for one repeated item
     fields:
       title: "h2::text"          # field_name: CSS selector
       url: "a::attr(href)"
   next_page: "a.next::attr(href)"  # optional; omit if there's no pagination
   ```

2. Run it:

   ```bash
   scrapy runspider src/spiders/generic_spider.py \
       -a config_path=src/spiders/configs/<target_name>.yaml
   ```

3. Add a fixture (a saved HTML snapshot) under `tests/fixtures/targets/` and
   a unit test in `tests/unit/spiders/` asserting `GenericSpider.parse()`
   extracts the fields you expect — following the pattern in
   `tests/unit/spiders/test_generic_spider.py`. In practice, every real
   target added so far (see `docs/TEST_TARGETS.md`) instead gets a live
   `tests/integration/test_<target>_live.py` that runs the real config
   against the real site via `scrapy runspider` in a subprocess and
   asserts on the real scraped content — `SpiderConfig`/`GenericSpider`
   themselves are already covered generically by `tests/unit/spiders/`,
   so a per-config test's job is to prove the *selectors* work against
   the real live page, which a static fixture can't do.

4. If the target requires anti-bot handling (Cloudflare or similar), set
   `antibot_needed: true` — this routes the request through
   `ByparrMiddleware` (requires `TITAN_BYPARR_URL` to be set; otherwise it
   falls back to a plain request and logs a warning, it never crashes the
   crawl). If the target only needs JS to render its content (no anti-bot
   challenge), set `render_js: true` instead, which uses
   `PlaywrightMiddleware`. Either way: config only, never target-specific
   code.
