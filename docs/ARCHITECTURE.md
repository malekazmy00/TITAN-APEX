# Architecture (Phase 1 snapshot)

TITAN-APEX is built around three abstract interfaces in `src/core/interfaces/`
so that concrete providers can be swapped without touching the code that
depends on them:

- **`AntibotProvider`** — `solve(url) -> Solution`. No implementation yet
  (Phase 3 will add `src/providers/antibot/byparr_provider.py`).
- **`StorageBackend`** — `save(item)`, `query(filters)`, `close()`.
  Implemented today by `src/providers/storage/sqlite_backend.py`.
- **`AIAnalyzer`** — `analyze(text) -> AnalysisResult`. No implementation
  yet (Phase 5, on the GPU lab machine).

## Data flow (Phase 1)

```
configs/*.yaml --> spider_config.load_spider_config() --> SpiderConfig
                                                              |
                                                              v
                                                     GenericSpider.parse()
                                                              |
                                                    (dict items, one per row)
                                                              |
                                        RetryBackoffMiddleware (downloader)
                                                              |
                                                     StorageBackend.save()
```

Every module logs through `src/logging_config.get_logger()`, which emits
one JSON object per line — no `print()` calls anywhere outside a CLI entry
point.

## Why config-driven spiders

A single `GenericSpider` reads a target's URL, CSS selectors and rate
limit from a YAML file (`src/spiders/configs/*.yaml`). Adding a new target
never means writing new spider code — see `ADDING_A_TARGET.md`.

## Contract testing

Any new implementation of an interface (a new storage backend, a new
antibot provider, ...) must pass the matching suite under `tests/contract/`
before it is accepted. See `tests/contract/test_storage_backend_contract.py`
for the pattern.
