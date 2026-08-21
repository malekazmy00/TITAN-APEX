# TITAN-APEX

Core of an OSINT / web-scraping platform, built around swappable interfaces
(`AntibotProvider`, `StorageBackend`, `AIAnalyzer`) and config-driven
spiders. See `docs/REQUIREMENTS.md` for the full build plan (and the
"Pending Real-Network Verification" section for what still needs proving
on a VPS) and the rules every change must follow, and `docs/ARCHITECTURE.md`
for how it all fits together.

## Status: through Phase 3

- Interfaces: `src/core/interfaces/{antibot_provider,storage_backend,ai_analyzer}.py`
- Config-driven spider: `src/spiders/generic_spider.py` — targets in
  `src/spiders/configs/*.yaml`
- Middlewares: `retry_backoff.py`, `circuit_breaker.py`,
  `playwright_middleware.py`, `byparr_middleware.py`
- Structured JSON logging: `src/logging_config.py`
- Storage backend: `src/providers/storage/sqlite_backend.py`
- Antibot provider: `src/providers/antibot/byparr_provider.py`

## Quickstart

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# lint / type-check / test
ruff check src/ tests/
mypy src/ --strict
pytest tests/unit -v --cov=src --cov-fail-under=85
pytest tests/contract -v

# run the first real spider
scrapy runspider src/spiders/generic_spider.py \
    -a config_path=src/spiders/configs/quotes_toscrape.yaml
```

## Adding a new target

See `docs/ADDING_A_TARGET.md` — it's a new YAML config, not new code.
