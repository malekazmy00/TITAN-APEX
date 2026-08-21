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
   `tests/unit/spiders/test_generic_spider.py`.

4. If the target requires anti-bot handling, set `antibot_needed: true` in
   the config (Phase 3+ — not wired up yet in Phase 1) instead of adding
   target-specific code.
