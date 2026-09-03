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
    # docs/REQUIREMENTS.md section 9 entry 23 (Known Limitation #5's real
    # fix): a target whose items have no stable class/attribute at all
    # (a CSS-in-JS SPA -- styled-components/emotion -- generates a fresh,
    # opaque class name every rebuild) can't use `item`/`fields` in their
    # normal (per-item-container + CSS-descendant-field) sense at all --
    # there is no class name a config written today can still rely on
    # tomorrow. When set, `item` is reinterpreted as a *flat* selector
    # matching every field-element of every item concatenated together in
    # document order, and `fields` values become
    # `"{offset}::text"`/`"{offset}::attr(name)"` (0-indexed position
    # *within* one group of `item_group_size` consecutive matches) --
    # see src.providers.antibot.parsed_html.extract_positional_html_items's
    # own docstring for the full mechanism. None (the default) keeps
    # every existing config's exact prior (descendant-selector) behavior.
    item_group_size: int | None = Field(default=None, gt=0)


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
    # docs/REQUIREMENTS.md section 9 entry 21, Step 1 (Referer path
    # consistency + session warm-up, Levels 1/2 -- Level 3's session-wide
    # delayed classification is a separate, later step): visited in
    # order, each one via a real Scrapy request chain (GenericSpider's
    # own `_parse_warm_session_step`), *before* any of `start_urls` --
    # not a cosmetic pre-request, a real navigation hop each of Scrapy's
    # own already-enabled `RefererMiddleware`/`CookiesMiddleware` sees
    # and acts on exactly like any other in-crawl navigation, so the
    # real Referer chain and any real session cookies a target sets
    # along the way are both genuinely present by the time `start_urls`
    # is finally reached -- not simulated after the fact. Empty (the
    # default) keeps every existing config's exact prior behavior: a
    # direct request to `start_urls` with no warm-up hop at all.
    warm_session_urls: list[str] = Field(default_factory=list)
    # docs/REQUIREMENTS.md section 9 entry 21, Step 2: opt-in, per-target
    # -- when True and antibot_needed is also True, the real browser-
    # driving provider (Camoufox/Patchright) starts this solve() call's
    # browser context from a cross-call accumulated cookie/storage
    # profile, and saves its own resulting state back into it on
    # success (src.providers.antibot.cookie_jar_manager's own module
    # docstring has the full mechanism). False (the default) keeps
    # every existing target's exact prior behavior: a genuinely fresh,
    # empty browser profile every single solve() call -- the same
    # complete isolation entry 17's own test suite depends on.
    use_accumulated_profile: bool = False
    # Real, evidenced gap (docs/REQUIREMENTS.md section 9 entry 24 --
    # scrapethissite.com/pages/advanced/?gotcha=headers's own
    # "User-Agent doesn't look like a standard mozilla/chrome/safari
    # value" check, and this project's own finding that render_js was
    # the *only* currently-possible fix, since no per-target UA field
    # existed at all before this one). None (the default) keeps every
    # existing target's exact prior behavior: each provider sends
    # whatever real User-Agent it always has (Camoufox's real Firefox
    # UA, Patchright/PlaywrightMiddleware's real Chromium UA, Byparr's
    # own upstream default) -- zero change unless a config opts in.
    # Applies only to the 3 AntibotProvider implementations
    # (byparr/camoufox/patchright), not PlaywrightMiddleware's own,
    # separate render_js path -- see AntibotProvider.solve()'s own
    # docstring for the full per-provider contract (Byparr's /v1
    # protocol has no such field at all, confirmed by reading its own
    # source -- a real, documented, best-effort-only gap, not a bug).
    user_agent_override: str | None = None
    # docs/REQUIREMENTS.md section 9 entry 30 ("الطبقة 3" -- Strategy
    # Engine): per-target opt-in for StrategyCapability.ADJUST_BACKOFF
    # (src/strategy/strategy_capability.py) -- None (every existing
    # config's unchanged default) means this target has not opted into
    # backoff adjustment at all, so CircuitBreakerMiddleware never even
    # consults the Strategy Engine for it (see that middleware's own
    # _resolve_cooldown). When set, this is the multiplier *this
    # target* wants applied on top of a classified failure's own base
    # cooldown -- always clamped again at decide-time to
    # StrategyEngineConfig.adjust_backoff_max_multiplier (the absolute,
    # env-configured ceiling), so a target can request less than the
    # ceiling but never more.
    strategy_backoff_multiplier: float | None = Field(default=None, gt=1.0, le=5.0)

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

    @model_validator(mode="after")
    def _item_group_size_not_yet_supported_with_live_dom_or_progressive(self) -> SpiderConfig:
        # docs/REQUIREMENTS.md section 9 entry 23: item_group_size's
        # positional extraction is only implemented for the default,
        # single-read "parsed_html" path so far (generic_spider.py's own
        # _parse_html) -- deliberately narrower scope than entry 14's
        # live_dom/progressive_extraction machinery, per this entry's own
        # explicit instruction to document a sub-gap separately rather
        # than solving everything in one pass. A config combining them
        # must fail loudly here, at load time, rather than silently
        # falling back to the wrong (descendant-selector) interpretation
        # of `item`/`fields` at parse time.
        if self.selectors is None or self.selectors.item_group_size is None:
            return self
        if self.extraction_mode == "live_dom":
            raise ValueError(
                "selectors.item_group_size is not yet supported with extraction_mode "
                "'live_dom' -- only the default 'parsed_html' extraction path "
                "implements positional/group extraction so far"
            )
        if self.progressive_extraction:
            raise ValueError(
                "selectors.item_group_size is not yet supported with "
                "progressive_extraction: true -- only a single, non-progressive "
                "read implements positional/group extraction so far"
            )
        return self

    @model_validator(mode="after")
    def _use_accumulated_profile_requires_a_real_browser_provider(self) -> SpiderConfig:
        if not self.use_accumulated_profile:
            return self
        # Same real structural requirement as login/extraction_mode/
        # progressive_extraction: only a provider with a real browser
        # context (camoufox/patchright) has anything to load a saved
        # profile into or save one back out of at all (docs/REQUIREMENTS.md
        # section 9 entry 21, Step 2) -- ByparrProvider's own /v1 API
        # never hands this process a browser context.
        if not self.antibot_needed:
            raise ValueError("use_accumulated_profile requires antibot_needed: true")
        if self.antibot_provider not in ("camoufox", "patchright"):
            raise ValueError(
                "use_accumulated_profile requires antibot_provider 'camoufox' or "
                f"'patchright' (a real browser context) -- got {self.antibot_provider!r}"
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
