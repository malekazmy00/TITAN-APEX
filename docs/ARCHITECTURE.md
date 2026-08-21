# Architecture (through Phase 5)

TITAN-APEX is built around three abstract interfaces in `src/core/interfaces/`
so that concrete providers can be swapped without touching the code that
depends on them:

- **`AntibotProvider`** — `solve(url) -> Solution`. Implemented by
  `src/providers/antibot/byparr_provider.py` (Phase 3).
- **`StorageBackend`** — `save(item)`, `query(filters)`, `close()`.
  Implemented by `src/providers/storage/sqlite_backend.py` (Phase 1).
- **`AIAnalyzer`** — `analyze(text) -> AnalysisResult`. Implemented by
  `src/ai_analysis/ollama_analyzer.py` (Phase 5). Code is fully tested;
  live inference on a real GPU is tracked as pending in
  `docs/REQUIREMENTS.md` section 5 (a legitimate hardware exception —
  see below).

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
before it is accepted. See `tests/contract/test_storage_backend_contract.py`,
`tests/contract/test_antibot_provider_contract.py`, and
`tests/contract/test_ai_analyzer_contract.py` for the pattern.

## Task queue (Phase 4)

`src/queue/enqueue.py` pushes a crawl job (a target config path) onto a
Redis-backed RQ queue. `src/queue/tasks.run_spider_job` is what an RQ
worker actually executes — it shells out to `scrapy runspider` in a
**subprocess**, deliberately: Twisted's reactor can only be installed
once per process, so a long-lived worker processing more than one crawl
in-process would crash on the second job. A failed crawl raises
`QueueError`, so RQ's own job-failure tracking (not a silently "succeeded"
job) reflects reality.

## Alerting (Phase 4)

"فشل متكرر = تنبيه" (repeated failure = alert): `src/alerting.py`
provides `AlertDispatcher`, wired into `CircuitBreakerMiddleware` — every
time a circuit opens (the failure-threshold consecutive-failures event),
an `AlertEvent` is sent. Delivery always logs at CRITICAL, and also POSTs
a JSON payload to `TITAN_ALERT_WEBHOOK_URL` when one is configured.
Webhook delivery failure is caught and logged (`alert.webhook_delivery_failed`)
— an alerting problem never crashes the crawl that triggered the alert.

## AI analysis (Phase 5)

`src/ai_analysis/ollama_analyzer.py` implements `AIAnalyzer` against a
local/remote Ollama instance (`/api/generate`), default model
**`qwen3:14b`** — a general-purpose instruction model, not
`qwen2.5-coder`, since this analyzes/summarizes scraped OSINT text rather
than reasoning about code. Output is never free text: Ollama's `format`
parameter is set to a JSON schema matching `AnalysisResult` exactly, so
the model is constrained to emit valid, schema-conformant JSON — parsed
and re-validated through `AnalysisResult.model_validate()` before it ever
reaches a caller. Every failure mode (connection error, malformed
envelope, non-JSON structured output, schema violation) raises
`AIAnalyzerError`, never returns a guessed or partial result.

GPU inference itself cannot be verified here or in CI (no GPU in either
environment) — see docs/REQUIREMENTS.md section 5 for the tracked pending
item and why that's a legitimate exception to the "prove it in CI first"
rule (a hardware constraint, not an assumed network/environment limit).
