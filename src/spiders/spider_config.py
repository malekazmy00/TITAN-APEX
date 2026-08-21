"""Loading and validation of per-target spider YAML configs.

Adding a new scrape target means adding a new ``configs/*.yaml`` file, not
writing new spider code (docs/REQUIREMENTS.md, section 1 — "مبدأ التوسع
الأساسي").
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from src.core.exceptions import ConfigError


class SelectorsConfig(BaseModel):
    """CSS selectors describing how to extract items from a page."""

    item: str
    fields: dict[str, str] = Field(min_length=1)


class SpiderConfig(BaseModel):
    """Validated, fully-typed representation of a target's YAML config."""

    name: str
    start_urls: list[str] = Field(min_length=1)
    allowed_domains: list[str] = Field(default_factory=list)
    rate_limit: float = Field(gt=0)
    selectors: SelectorsConfig
    next_page: str | None = None
    # Phase 2: dynamic content + self-throttling, both driven by config —
    # never by target-specific code (docs/REQUIREMENTS.md, section 2).
    render_js: bool = False
    max_concurrency: int = Field(default=2, gt=0)
    # Phase 3: anti-bot solving via Byparr, also config-driven.
    antibot_needed: bool = False
    # Round 3 (docs/REQUIREMENTS.md section 9 entry 4): more than one
    # AntibotProvider implementation exists now (ByparrProvider,
    # CamoufoxProvider), each genuinely stronger against different
    # challenge shapes (documented with real evidence in
    # docs/REQUIREMENTS.md's "Antibot provider comparison" -- never
    # assumed). Per-target selection, no code change needed to switch —
    # default "byparr" keeps every existing config's behavior unchanged.
    antibot_provider: Literal["byparr", "camoufox"] = "byparr"
    # Real, evidenced gaps found expanding Test Targets coverage
    # (docs/REQUIREMENTS.md, section 7, entries 3-4): some render_js
    # targets need a fixed extra dwell after navigation (a site-added
    # client-side render delay PlaywrightMiddleware's scroll loop doesn't
    # reliably cover), or a specific element clicked before the content
    # is there at all (e.g. a "Load More" button, never scroll-triggered).
    # Both are no-ops unless set.
    render_wait_ms: int | None = Field(default=None, gt=0)
    click_selector: str | None = None


def load_spider_config(path: str) -> SpiderConfig:
    """Load and validate a spider YAML config file.

    Raises:
        ConfigError: if the file is missing, is not valid YAML, is not a
            YAML mapping, or fails schema validation.
    """
    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(f"spider config not found: {path}")

    try:
        with config_path.open("r", encoding="utf-8") as handle:
            raw: Any = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ConfigError(f"spider config is not valid YAML: {path}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"spider config must be a YAML mapping at the top level: {path}")

    try:
        return SpiderConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"spider config failed schema validation: {path}: {exc}") from exc
