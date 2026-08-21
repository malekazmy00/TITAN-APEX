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

## Test Environment Security (docs/REQUIREMENTS.md section 8)

`test-environment/` (a self-hosted, Docker-based adversarial target for
`GenericSpider` to scrape — see `test-environment/README.md` for what
each layer is) runs under three mandatory security/isolation rules,
enforced regardless of which optional layers are enabled or disabled:

- **Network isolation, on two networks, not one.**
  `test-environment/docker-compose.test.yml` declares `test-environment`
  as `internal: true` — Docker refuses to give any container on it a
  default route to the public internet, and (confirmed by hand: a
  published port on a purely `internal: true` network is unreachable
  from the host, not just outbound-blocked) it isn't reachable from the
  host either. `mock-target` — the container that actually serves fake
  data, honeypots, and decoy data — has *only* this network. A second,
  normal (non-internal) `edge` network exists solely so Anubis's port
  can be published to the host, the same shape a real reverse proxy has
  (one leg on the protected backend network, one leg facing the world);
  Anubis is attached to both. This does give Anubis itself normal
  outbound access on its `edge` leg — an accepted, documented exception
  for the edge proxy specifically, never extended to `mock-target`.
  Building the images still needs normal internet access (pulling the
  Python base image, `pip install`ing `requirements.txt`) — that happens
  once at `docker compose build` time, before either runtime network
  exists, the same way the root `docker-compose.yml`'s own images are
  pulled.
- **100% fake data.** Every byte of content the mock target serves
  (`test-environment/mock-target/content_generator.py`) is generated by
  Faker, seeded per session for determinism. There is no code path in
  `test-environment/` that reads, embeds, or proxies any real data from
  any real source.
- **Per-container resource limits.** Every service in
  `docker-compose.test.yml` declares an explicit
  `deploy.resources.limits` (CPU and memory) — a bug in one container
  (an infinite loop, a memory leak) cannot starve the CI runner or the
  rest of the stack.

This is separate from, and in addition to, `test-environment/README.md`
section 1.4's per-layer "how to verify it's actually active" checks —
those prove a *challenge* layer is doing its job; the three rules above
are unconditional regardless of which challenge layers are on.
