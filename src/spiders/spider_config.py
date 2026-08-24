"""Loading and validation of per-target spider YAML configs.

Adding a new scrape target means adding a new ``configs/*.yaml`` file, not
writing new spider code (docs/REQUIREMENTS.md, section 1 — "مبدأ التوسع
الأساسي").
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

from src.core.exceptions import ConfigError


class SelectorsConfig(BaseModel):
    """CSS selectors describing how to extract items from a page."""

    item: str
    fields: dict[str, str] = Field(min_length=1)


class JsonSelectorsConfig(BaseModel):
    """Dotted-key paths describing how to extract items from a JSON API
    response (docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md's JSON/API
    round -- test-environment/mock-target's ``/api/feed``, built in an
    earlier round but never wired up to ``GenericSpider`` until now).

    A dotted path like ``"post.author"`` means ``item["post"]["author"]``
    -- there is no list-indexing or wildcard support, only nested-object
    traversal, which is all ``/api/feed``'s own shape (``docs``, "Semi-GraphQL
    ``/api/feed``") needs.
    """

    items_path: str
    fields: dict[str, str] = Field(min_length=1)
    # Both are optional together: a JSON API with no pagination at all
    # (a single response, no "next" concept) sets neither.
    next_cursor_path: str | None = None
    has_next_page_path: str | None = None


class LoginConfig(BaseModel):
    """POST + CSRF + session login-flow instructions for a target that
    requires it (docs/REQUIREMENTS.md section 9 entry 15, Known
    Limitation #1: login/session, activated ahead of Interstitials per
    explicit user request).

    ``username_field``/``password_field``/``submit_selector`` are CSS
    selectors for the real login form's own input/button elements --
    the CSRF token itself is never named here at all: it's a real hidden
    form field a real browser submits automatically, not something this
    layer reads or reconstructs.
    """

    login_url: str
    username: str
    password: str
    username_field: str
    password_field: str
    submit_selector: str
    # Test-only -- see src.core.interfaces.antibot_provider.LoginFlow's
    # own field of the same name for the full rationale. None (the
    # default) in every real target config.
    session_expiry_probe_url: str | None = None


class SpiderConfig(BaseModel):
    """Validated, fully-typed representation of a target's YAML config."""

    name: str
    start_urls: list[str] = Field(min_length=1)
    allowed_domains: list[str] = Field(default_factory=list)
    rate_limit: float = Field(gt=0)
    # response_format (docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md's
    # JSON/API round) picks which of the two selector styles below is
    # required: "html" (the original, CSS-based `selectors`) or "json"
    # (dotted-path-based `json_selectors`, for endpoints like
    # test-environment/mock-target's `/api/feed`) -- never both, never
    # neither, enforced by `_exactly_one_selectors_block_for_format` below.
    response_format: Literal["html", "json"] = "html"
    selectors: SelectorsConfig | None = None
    json_selectors: JsonSelectorsConfig | None = None
    next_page: str | None = None
    # Phase 2: dynamic content + self-throttling, both driven by config —
    # never by target-specific code (docs/REQUIREMENTS.md, section 2).
    render_js: bool = False
    max_concurrency: int = Field(default=2, gt=0)
    # Phase 3: anti-bot solving via Byparr, also config-driven.
    antibot_needed: bool = False
    # Round 3 (docs/REQUIREMENTS.md section 9 entry 4) added CamoufoxProvider;
    # this phase's revision added PatchrightProvider as a third, lighter
    # ("Chromium + stealth layer" rather than a whole separate Firefox-based
    # browser) option. Each is genuinely stronger against different
    # challenge shapes (documented with real evidence in
    # docs/REQUIREMENTS.md's "Antibot provider comparison" -- never
    # assumed). Per-target selection, no code change needed to switch —
    # default "byparr" keeps every existing config's behavior unchanged.
    antibot_provider: Literal["byparr", "camoufox", "patchright"] = "byparr"
    # Real, evidenced gaps found expanding Test Targets coverage
    # (docs/REQUIREMENTS.md, section 7, entries 3-4): some render_js
    # targets need a fixed extra dwell after navigation (a site-added
    # client-side render delay PlaywrightMiddleware's scroll loop doesn't
    # reliably cover), or a specific element clicked before the content
    # is there at all (e.g. a "Load More" button, never scroll-triggered).
    # Both are no-ops unless set.
    render_wait_ms: int | None = Field(default=None, gt=0)
    click_selector: str | None = None
    # docs/REQUIREMENTS.md section 9 entry 12: the real fix for entry 11's
    # confirmed Shadow DOM gap -- "parsed_html" (default, every existing
    # config's unchanged behavior) re-parses the provider's returned HTML
    # string with `selectors` after the fact; "live_dom" instead has the
    # provider itself extract items directly from the live browser page
    # (via Playwright's page.locator(), which auto-pierces *open* shadow
    # roots) before closing it, since a serialized HTML string never
    # carries shadow-root content at all regardless of which browser
    # produced it. Reuses the exact same `selectors` block either way --
    # no second selector language for a target to learn.
    extraction_mode: Literal["parsed_html", "live_dom"] = "parsed_html"
    # docs/REQUIREMENTS.md section 9 entry 14: the real fix for entry 13's
    # confirmed DOM Virtualization gap -- reading the page once, after
    # scrolling finishes (what both extraction_mode values do by default),
    # cannot recover content a virtualized list evicted along the way; it
    # is genuinely gone from the DOM by then. When True, the provider
    # instead extracts (or snapshots html, for "parsed_html") after
    # *every* scroll step and merges the results, deduplicated by post
    # id. Independent of extraction_mode -- both "parsed_html" and
    # "live_dom" can opt in, and get progressive collection each in their
    # own way. Defaults False: every existing config's behavior stays
    # exactly as entries 11-13 already established, zero regression risk.
    progressive_extraction: bool = False
    # docs/REQUIREMENTS.md section 9 entry 15: Known Limitation #1
    # (login/session), activated ahead of Interstitials per explicit
    # user request. None (the default) in every existing config's
    # unchanged behavior -- unauthenticated, exactly as before this
    # entry existed.
    login: LoginConfig | None = None

    @model_validator(mode="after")
    def _exactly_one_selectors_block_for_format(self) -> SpiderConfig:
        if self.response_format == "html":
            if self.selectors is None:
                raise ValueError("selectors is required when response_format is 'html'")
            if self.json_selectors is not None:
                raise ValueError("json_selectors must not be set when response_format is 'html'")
        else:
            if self.json_selectors is None:
                raise ValueError("json_selectors is required when response_format is 'json'")
            if self.selectors is not None:
                raise ValueError("selectors must not be set when response_format is 'json'")
        return self

    @model_validator(mode="after")
    def _live_dom_requires_a_real_browser_provider(self) -> SpiderConfig:
        if self.extraction_mode != "live_dom":
            return self
        # Every one of these is a real structural requirement, not a
        # style preference -- see extraction_mode's own field comment for
        # why: only a provider with a real, live browser page to query
        # (camoufox/patchright) can extract from a live DOM at all, that
        # page only exists behind antibot_needed's own solving path, and
        # response_format must be "html" since live DOM extraction reuses
        # `selectors`, not `json_selectors`.
        if self.response_format != "html":
            raise ValueError("extraction_mode 'live_dom' requires response_format 'html'")
        if not self.antibot_needed:
            raise ValueError("extraction_mode 'live_dom' requires antibot_needed: true")
        if self.antibot_provider not in ("camoufox", "patchright"):
            raise ValueError(
                "extraction_mode 'live_dom' requires antibot_provider 'camoufox' or "
                f"'patchright' (a real, live browser page) -- got {self.antibot_provider!r}"
            )
        return self

    @model_validator(mode="after")
    def _progressive_extraction_requires_a_real_browser_provider(self) -> SpiderConfig:
        if not self.progressive_extraction:
            return self
        # Same real structural requirements as extraction_mode: "live_dom"
        # (only a real, live browser page can be scrolled and re-read
        # step by step at all) -- independent of extraction_mode's own
        # value, so checked here rather than folded into the validator
        # above.
        if self.response_format != "html":
            raise ValueError("progressive_extraction requires response_format 'html'")
        if not self.antibot_needed:
            raise ValueError("progressive_extraction requires antibot_needed: true")
        if self.antibot_provider not in ("camoufox", "patchright"):
            raise ValueError(
                "progressive_extraction requires antibot_provider 'camoufox' or "
                f"'patchright' (a real, live browser page) -- got {self.antibot_provider!r}"
            )
        return self

    @model_validator(mode="after")
    def _login_requires_a_real_browser_provider(self) -> SpiderConfig:
        if self.login is None:
            return self
        # Same real structural requirement as extraction_mode/
        # progressive_extraction: only a provider with a real, live
        # browser page (camoufox/patchright) can fill and submit a real
        # login form at all (docs/REQUIREMENTS.md section 9 entry 15).
        if not self.antibot_needed:
            raise ValueError("login requires antibot_needed: true")
        if self.antibot_provider not in ("camoufox", "patchright"):
            raise ValueError(
                "login requires antibot_provider 'camoufox' or 'patchright' "
                f"(a real, live browser page) -- got {self.antibot_provider!r}"
            )
        return self


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
