# Deployment — TITAN-APEX

This document reflects the current implementation through Phase 5. The repository now includes Redis/RQ queueing, Byparr anti-bot solving, in-process Camoufox/Patchright browser providers, Playwright rendering, SQLite storage, structured logging/alerting, and optional Ollama AI analysis.

## 1. Runtime layout

The recommended production split is:

- **VPS:** Scrapy, Playwright/Camoufox/Patchright, Byparr, Redis, and RQ workers.
- **AI lab:** Ollama with `qwen3:14b` on the RTX 4070S machine.
- **SQLite:** local persistent storage for the current single-node deployment.

The AI layer is intentionally separate from the crawler host. Live GPU inference remains the only hardware-specific verification item tracked in `docs/REQUIREMENTS.md`.

## 2. Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
playwright install --with-deps chromium
```

Camoufox and Patchright are installed by the project dependency set. Their browser/runtime setup should be completed on the VPS before enabling targets that select those providers.

## 3. Environment configuration

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Important settings include:

- `TITAN_BYPARR_URL` — Byparr HTTP endpoint.
- `TITAN_REDIS_URL` — Redis connection URL used by RQ.
- `TITAN_SQLITE_PATH` — SQLite database path.
- `TITAN_OLLAMA_URL` — Ollama endpoint for Phase 5 analysis.
- `TITAN_AI_MODEL` — defaults to `qwen3:14b`.
- `TITAN_ALERT_WEBHOOK_URL` — optional webhook for repeated-failure alerts.
- `TITAN_PLAYWRIGHT_EXECUTABLE` — optional explicit Chromium executable.

Do not commit `.env` or credentials.

## 4. Start infrastructure

The supplied Compose file starts the crawler-side services:

```bash
docker compose up -d
```

This starts Byparr on host port `8191` and Redis on host port `6379`, with a persistent Redis volume.

For host-side execution:

```bash
TITAN_BYPARR_URL=http://localhost:8191
TITAN_REDIS_URL=redis://localhost:6379/0
```

When the crawler runs inside the Compose network, use `http://byparr:8191` and `redis://redis:6379/0` instead.

## 5. Run a crawl directly

The generic spider is config-driven; target-specific Python code is not required.

```bash
scrapy runspider src/spiders/generic_spider.py \
  -a config_path=src/spiders/configs/quotes_toscrape.yaml
```

For targets that require a browser provider, select it in YAML with `antibot_provider: camoufox` or `antibot_provider: patchright`.

## 6. Run the queue worker

Phase 4 jobs execute each crawl in a separate subprocess so a long-lived RQ worker never reuses a Twisted reactor between jobs.

Start a worker from the repository root:

```bash
rq worker --url "$TITAN_REDIS_URL" titan
```

Use the project's enqueue helper for configured crawls:

```bash
python -m src.queue.enqueue src/spiders/configs/quotes_toscrape.yaml
```

Keep the queue name/options aligned with `src/queue/enqueue.py` and `src/queue/connection.py`; environment configuration remains the source of truth for connection details.

## 7. Ollama AI host

On the GPU machine:

```bash
ollama serve
ollama pull qwen3:14b
```

Configure the crawler environment with the reachable Ollama endpoint:

```bash
TITAN_OLLAMA_URL=http://<ollama-host>:11434
TITAN_AI_MODEL=qwen3:14b
```

The real GPU verification must be performed on the RTX 4070S host and recorded in `docs/REQUIREMENTS.md` only after the model produces semantically sensible `summary` and `entities` values.

## 8. Production verification

Before deployment:

```bash
ruff check src/ tests/
mypy src/ --strict
pytest tests/unit -v --cov=src --cov-fail-under=85
pytest tests/contract -v
```

Run the integration suite appropriate to the available services:

```bash
pytest tests/integration -v
```

A live-test skip is not evidence that the skipped capability works.

## 9. Operational rules

- Keep Redis persistent and monitored; an RQ worker without Redis is not a functioning queue deployment.
- Keep Byparr reachable from the same network namespace as the crawler process that invokes it.
- Do not move the SQLite database onto an ephemeral container filesystem.
- Configure an alert webhook for production if repeated crawl failures need external notification.
- Keep the AI lab endpoint private; expose Ollama only to the crawler host/network that needs it.
- Do not bypass the configured anti-bot policy when a provider fails; provider failures must remain visible in logs and follow the documented fallback behavior.
- Review `docs/REQUIREMENTS.md` before adding a provider or target. New providers must pass the matching contract suite.

## 10. Current verification boundary

The implementation is through Phase 5. The remaining tracked verification item is real inference against `qwen3:14b` on the RTX 4070S. This is a hardware verification boundary, not an assumed network limitation.
