# Architecture (through Phase 3)

TITAN-APEX is built around three abstract interfaces in `src/core/interfaces/`
so that concrete providers can be swapped without touching the code that
depends on them:

- **`AntibotProvider`** — `solve(url) -> Solution`. Implemented by
  `src/providers/antibot/byparr_provider.py` (Phase 3).
- **`StorageBackend`** — `save(item)`, `query(filters)`, `close()`.
  Implemented by `src/providers/storage/sqlite_backend.py` (Phase 1).
- **`AIAnalyzer`** — `analyze(text) -> AnalysisResult`. No implementation
  yet (Phase 5, on the GPU lab machine).

## Data flow

```
configs/*.yaml --> spider_config.load_spider_config() --> SpiderConfig
                                                              |
                                                              v
                                                    GenericSpider.start()
                                                (meta: playwright, antibot_needed)
                                                              |
                                        downloader middleware chain, in order:
                                          ByparrMiddleware        (520)
                                          PlaywrightMiddleware    (543)
                                          RetryBackoffMiddleware  (550)
                                          CircuitBreakerMiddleware(900)
                                                              |
                                                     GenericSpider.parse()
                                                              |
                                                    (dict items, one per row)
                                                              |
                                                StorageBackendPipeline
                                                              |
                                                     StorageBackend.save()
```

Middleware order is deliberate (see each module's docstring): lower
priority number = closer to the Engine = checked first when a request is
about to go out, so `CircuitBreakerMiddleware` (900, closest to the
Downloader) is the last gate before a real network call — it blocks before
`ByparrMiddleware`/`PlaywrightMiddleware` waste a solve/render, and it
observes every raw response/exception *before* `RetryBackoffMiddleware`
decides whether to retry it.

Every module logs through `src/logging_config.get_logger()`, which emits
one JSON object per line — no `print()` calls anywhere outside a CLI entry
point.

## Why config-driven spiders

A single `GenericSpider` reads a target's URL, CSS selectors, rate limit,
and per-target feature flags (`render_js`, `antibot_needed`,
`max_concurrency`) from a YAML file (`src/spiders/configs/*.yaml`). Adding
a new target never means writing new spider code — see
`ADDING_A_TARGET.md`.

## Fallback, never a crash

`ByparrMiddleware` and `PlaywrightMiddleware` both fail the same way: on
error, they log clearly (`*.solve_failed_fallback` / `RenderError`) and
either fall back to a plain download (Byparr) or surface a typed exception
that Scrapy reports without killing the crawl (Playwright). No provider
failure is silent, and none of them takes the whole crawl down.

## Contract testing

Any new implementation of an interface (a new storage backend, a new
antibot provider, ...) must pass the matching suite under `tests/contract/`
before it is accepted. See `tests/contract/test_storage_backend_contract.py`
and `tests/contract/test_antibot_provider_contract.py` for the pattern.
