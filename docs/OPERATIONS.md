# Operations Runbook — TITAN-APEX

## Service map

| Service | Role | Required when |
|---|---|---|
| Scrapy | Crawl execution | Always |
| Redis | Queue backend | Queue/RQ deployment |
| RQ worker | Asynchronous crawl execution | Queue/RQ deployment |
| Byparr | External anti-bot browser service | Targets using `antibot_provider: byparr` |
| Camoufox | In-process browser provider | Targets using `antibot_provider: camoufox` |
| Patchright | In-process Chromium provider | Targets using `antibot_provider: patchright` |
| SQLite | Current storage backend | Always for the current implementation |
| Ollama | AI analysis | Targets/workflows using Phase 5 analysis |

## Start/stop order

1. Start Redis.
2. Start Byparr when any configured target uses the Byparr provider.
3. Start RQ workers when jobs are queued asynchronously.
4. Start the crawler or enqueue jobs.
5. Start/verify Ollama separately when AI analysis is enabled.

Stopping a worker does not delete queued jobs. Stopping Redis makes queue operations unavailable until Redis returns.

## Queue troubleshooting

Check the Redis endpoint first:

```bash
redis-cli -u "$TITAN_REDIS_URL" ping
```

Then inspect the worker process and its logs. A crawl job is deliberately executed in a subprocess because Twisted's reactor cannot be safely restarted inside the same long-lived Python process.

If a job repeatedly fails:

- verify the target config exists;
- verify the worker can reach the configured target/network;
- verify Byparr is reachable when required;
- inspect the crawl failure rather than treating the RQ job as successful;
- check the circuit-breaker/alert logs for repeated upstream failures.

## Byparr troubleshooting

Verify the configured endpoint from the same network namespace as the crawler:

```bash
curl -fsS "$TITAN_BYPARR_URL" >/dev/null
```

A Byparr failure is not a reason to silently fall back to an unrelated anti-bot mechanism. The provider/middleware layer logs the failure and follows its documented fallback behavior.

## Browser-provider troubleshooting

For `camoufox` and `patchright` targets:

- confirm the provider is selected in the target YAML;
- confirm browser dependencies are installed on the host;
- keep `max_concurrency` conservative for browser-heavy targets;
- inspect provider logs for browser launch, navigation, click, and extraction failures;
- use the matching integration test before declaring a target operational.

`live_dom` and `progressive_extraction` require a real browser provider. The config validator rejects invalid provider combinations before a crawl starts.

## Storage troubleshooting

SQLite is currently the production storage backend for the single-node architecture. Keep the database on persistent storage and ensure the process has write permissions.

If storage errors appear during a crawl, treat them as real crawl failures. Do not delete the database as a first response; preserve it for diagnosis unless corruption is independently established.

## Alerting

Repeated circuit-breaker failures generate a CRITICAL log event. If `TITAN_ALERT_WEBHOOK_URL` is configured, the alert dispatcher also sends a JSON payload to that endpoint.

If alerts stop arriving:

1. verify the webhook environment variable is present;
2. inspect the application logs for `alert.webhook_delivery_failed`;
3. test the webhook independently;
4. remember that a webhook delivery failure must not crash the crawl that generated the alert.

## Ollama troubleshooting

On the AI host:

```bash
ollama list
curl -fsS http://127.0.0.1:11434/api/tags >/dev/null
```

Confirm that `qwen3:14b` is present and that the crawler's `TITAN_OLLAMA_URL` points to the reachable endpoint.

The analyzer requires structured JSON conforming to `AnalysisResult`. Connection errors, malformed envelopes, invalid JSON, and schema violations are application errors; the analyzer does not return guessed partial results.

The final real-world verification step is semantic: run a real scraped text through the actual model and inspect whether `summary` and `entities` are sensible. A syntactically valid JSON response alone does not close the GPU verification item.

## Health verification before production changes

Run:

```bash
ruff check src/ tests/
mypy src/ --strict
pytest tests/unit -v --cov=src --cov-fail-under=85
pytest tests/contract -v
```

For changes affecting real services, also run the relevant integration tests and preserve the CI result as evidence. Do not convert a skipped live test into a claim of capability.

## Change discipline

- Add a target through `src/spiders/configs/*.yaml` whenever the generic spider can express the target.
- Add a new provider by implementing the existing interface and its contract suite.
- Keep external endpoints in environment/configuration rather than hardcoding them.
- Record unresolved real-environment gaps in `docs/REQUIREMENTS.md` instead of hiding them in comments or tests.
- Keep historical failed attempts in the requirements/changelog evidence when a capability requires iterative CI verification.
