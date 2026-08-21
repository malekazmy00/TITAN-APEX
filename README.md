# TITAN-APEX

Core of an OSINT / web-scraping platform, built around swappable interfaces
(`AntibotProvider`, `StorageBackend`, `AIAnalyzer`) and config-driven
spiders. See `docs/REQUIREMENTS.md` for the full build plan (section 5,
"Pending Real-Network Verification", tracks anything not yet proven in a
real environment — currently one item: live GPU inference for Phase 5,
a legitimate hardware exception, not a network assumption) and the rules
every change must follow, and `docs/ARCHITECTURE.md` for how it all fits
together.

## Status: through Phase 5

- Interfaces: `src/core/interfaces/{antibot_provider,storage_backend,ai_analyzer}.py`
- Config-driven spider: `src/spiders/generic_spider.py` — targets in
  `src/spiders/configs/*.yaml`
- Middlewares: `retry_backoff.py`, `circuit_breaker.py`,
  `playwright_middleware.py`, `byparr_middleware.py`
- Structured JSON logging: `src/logging_config.py`
- Storage backend: `src/providers/storage/sqlite_backend.py`
- Antibot provider: `src/providers/antibot/byparr_provider.py`
- Task queue: `src/queue/` (Redis + RQ)
- Alerting: `src/alerting.py` (repeated failure → CRITICAL log + optional webhook)
- AI analyzer: `src/ai_analysis/ollama_analyzer.py` (Ollama, `qwen3:14b`
  by default — structured JSON output only; live GPU inference pending
  the lab machine, see `docs/REQUIREMENTS.md` section 5)

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
