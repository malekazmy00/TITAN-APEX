"""Contract for anti-bot solving providers (e.g. Byparr, a future service).

This module defines the abstract contract only. No concrete implementation
lives here — see ``src/providers/antibot/`` for implementations (Phase 3).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class LiveDomSelectors(BaseModel):
    """Item/field CSS selectors for :meth:`AntibotProvider.solve`'s
    ``extraction_selectors`` parameter -- a provider that drives a real,
    live browser page may use these to extract items directly from the
    live DOM instead of returning HTML for the caller to re-parse later.

    Deliberately its own model here, not an import of
    ``src.spiders.spider_config.SelectorsConfig`` (structurally
    identical) -- ``core.interfaces`` is this project's innermost layer;
    it must not depend on ``spiders``, which itself depends on ``core``
    (docs/REQUIREMENTS.md, section 1's layering).

    ``fields`` values use the same parsel/Scrapy CSS-extension
    mini-language ``SelectorsConfig.fields`` already does (``"::text"``,
    ``"::attr(name)"``) -- see
    ``src.providers.antibot._live_dom.extract_live_dom_items`` for the
    real implementation of why that's exactly reused, not a new syntax.
    """

    item: str
    fields: dict[str, str] = Field(min_length=1)


class Solution(BaseModel):
    """Result of successfully solving an anti-bot challenge for a URL."""

    url: str
    html: str
    status_code: int
    cookies: dict[str, str] = Field(default_factory=dict)
    solved_at: datetime
    # None: extraction_selectors wasn't passed to solve() (the default,
    # every existing call site unchanged), or this provider can't support
    # live-DOM extraction (ByparrProvider) and only logged a warning
    # instead -- either way, the caller falls back to parsing `html`
    # itself, exactly as it always has. A list (even an empty one): the
    # provider genuinely performed live-DOM extraction and this *is* the
    # real result -- the caller must use these items as-is, not also
    # re-parse `html` (docs/REQUIREMENTS.md section 9 entry 12).
    items: list[dict[str, Any]] | None = None
    # docs/REQUIREMENTS.md section 9 entry 14: populated only when
    # progressive_extraction=True *and* extraction_selectors was NOT set
    # (the "parsed_html" progressive path -- extraction_selectors set at
    # the same time means "live_dom" progressive instead, which populates
    # `items`, not this). None means "not requested / not supported" (the
    # single, final `html` snapshot is still what it always was); a list
    # is every HTML snapshot captured across the provider's own scroll
    # steps, in order -- the caller merges/dedupes them itself, since only
    # the caller (GenericSpider) knows which field is the identity key.
    html_snapshots: list[str] | None = None


class AntibotProvider(ABC):
    """Abstract contract every anti-bot provider implementation must follow.

    Implementations are swappable: the rest of the codebase depends only on
    this interface, never on a concrete provider (docs/REQUIREMENTS.md,
    section 1 — "مبدأ التوسع الأساسي").
    """

    @abstractmethod
    def solve(
        self,
        url: str,
        click_selector: str | None = None,
        extraction_selectors: LiveDomSelectors | None = None,
        progressive_extraction: bool = False,
    ) -> Solution:
        """Solve whatever anti-bot challenge protects ``url``.

        ``click_selector`` (docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md,
        cookie-consent-wall round): an optional CSS selector for an
        element to click after the page loads -- e.g. a cookie-consent
        "Accept" button/link that gates real content -- before reading the
        page and returning a :class:`Solution`. This is **best-effort, not
        part of the required contract**: a provider that drives a real
        browser (in-process) can click; a provider that only delegates to
        an external HTTP-only solving service structurally may not be able
        to. A provider that cannot support it must not crash or silently
        drop it -- it must log a clear warning identifying exactly what
        was skipped and why, then proceed to solve without clicking, so
        the gap is visible in evidence rather than hidden.

        ``extraction_selectors`` (docs/REQUIREMENTS.md section 9 entry 12,
        ``SpiderConfig.extraction_mode: "live_dom"``): same best-effort
        contract as ``click_selector``, for a real, evidenced gap
        ``click_selector``'s own real-browser capability cannot close --
        driving a real browser past a challenge is not enough when the
        real target content lives inside a Shadow DOM (entry 11): a
        serialized HTML string (what ``Solution.html`` always is) never
        carries a shadow root's content at all, regardless of which
        browser produced it. A provider with a real, live browser page can
        extract items directly from that live DOM instead (piercing
        *open* shadow roots automatically) and return them via
        :attr:`Solution.items`; a provider with no live page to query
        (``ByparrProvider``) must not crash or silently drop this either
        -- log a clear warning and solve without it, leaving
        ``Solution.items`` as ``None`` so the caller falls back to parsing
        ``html`` itself.

        ``progressive_extraction`` (docs/REQUIREMENTS.md section 9 entry
        14, the real fix for a real, evidenced gap ``extraction_selectors``
        alone does *not* close): reading the page once, after scrolling
        finishes, cannot recover content a virtualized list (entry 13)
        evicted along the way -- it is genuinely, unambiguously gone from
        the DOM by then, not merely hidden or encapsulated, so neither
        ``html`` nor live-DOM extraction helps if read only at the end.
        When ``True``, a provider with a real, live browser page instead
        extracts (or snapshots ``html``) after *every* scroll step, not
        just the last, and merges the results (deduplicated by item
        identity) -- via :attr:`Solution.items` when ``extraction_selectors``
        is also set, or via :attr:`Solution.html_snapshots` (multiple raw
        HTML strings, one per step, for the caller to parse and merge
        itself) when it isn't. Same best-effort contract as the other two
        parameters: a provider with no live page to query (``ByparrProvider``)
        must not crash or silently drop it -- log a clear warning and solve
        without it, leaving both fields as they'd be without this flag.

        Implementations must raise
        :class:`src.core.exceptions.AntibotError` (never a bare
        ``Exception``) on failure, and must release any external resource
        they acquire (browser handle, HTTP connection, ...) in a
        ``finally`` block even when solving fails.
        """
        raise NotImplementedError
