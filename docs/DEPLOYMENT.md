# Deployment (Phase 1)

Phase 1 has no external services (no Redis, no Byparr, no Postgres) — the
only dependency is a Python 3.11+ environment and, for live crawls, network
access to the target site.

## Local / VPS setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
playwright install --with-deps chromium   # needed from Phase 2 onward
```

## Configuration

Copy `.env.example` to `.env` and adjust. All settings are read from the
environment by `src/settings.py` — nothing is hardcoded.

## Running a crawl

```bash
scrapy runspider src/spiders/generic_spider.py \
    -a config_path=src/spiders/configs/quotes_toscrape.yaml \
    -s DOWNLOADER_MIDDLEWARES='{"src.middlewares.retry_backoff.RetryBackoffMiddleware": 550}'
```

## Later phases

`docker-compose.yml` will start Redis, Byparr and (optionally) PostgreSQL
once Phase 3/4 wire them in — it is a placeholder in Phase 1.
