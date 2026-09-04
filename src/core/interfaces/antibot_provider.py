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


class LoginFlow(BaseModel):
    """Best-effort login-flow instructions for :meth:`AntibotProvider.solve`
    -- docs/REQUIREMENTS.md section 9 entry 15 (Known Limitation #1:
    login/session, activated ahead of Interstitials per explicit user
    request).

    Only a provider with a real, live browser page (Camoufox/Patchright)
    can fill a real DOM form and follow the real navigation a POST
    triggers; ``ByparrProvider``'s ``/v1`` API has no interaction
    capability at all -- same best-effort contract as
    ``click_selector``/``extraction_selectors``: a provider that can't
    support this must not crash or silently drop it, only log a clear
    warning and solve without it.

    A real, discovered architectural constraint this shape works around
    rather than assumes away: each :meth:`AntibotProvider.solve` call
    launches (and tears down) its own fresh browser -- cookies never
    persist *across* separate ``solve()`` calls **by default**. So
    "session persistence" here is demonstrated *within* one browser
    session across multiple in-browser navigations (login, then the
    actual target URL, then optionally a next-page link) inside a single
    ``solve()`` call, not across separate Scrapy-level requests reusing
    a live, shared browser process.

    **Update (docs/REQUIREMENTS.md section 9 entry 21, Step 2):** the
    "materially bigger architecture change" this docstring used to say
    was out of scope has since landed, in a specific, narrower form --
    :meth:`AntibotProvider.solve`'s own ``use_accumulated_profile``
    parameter persists cookies/storage state *across* separate
    ``solve()`` calls via a real, file-backed cookie jar
    (:mod:`src.providers.antibot.cookie_jar_manager`), not by keeping one
    live browser process/context open between calls (that specific
    "share one continuous session between requests" architecture is
    still not what this does, and still isn't needed for the real,
    demonstrated goal: a new browser starting from previously-accumulated
    state looks the same, from the target's own perspective, as one that
    never closed at all).

    ``username_field``/``password_field``/``submit_selector`` are CSS
    selectors for the real DOM form's own input/button elements. The
    CSRF token itself is never read or reconstructed by this layer at
    all -- it's a real hidden form field the browser submits
    automatically along with everything else, the same way a genuine
    user's browser would.
    """

    login_url: str
    username: str
    password: str
    username_field: str
    password_field: str
    submit_selector: str
    # Test-only (see test-environment/mock-target/app.py's own
    # /test-expire-session route docstring for why this exists at all):
    # when set, visited once immediately after a successful login --
    # before the real target URL -- to deterministically force this
    # session to be treated as already-expired, so a live test can
    # trigger session-expiry *detection* without a real, flaky
    # multi-second TTL wait. None (the default) in every real config.
    session_expiry_probe_url: str | None = None


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
        login_flow: LoginFlow | None = None,
        warm_session_urls: list[str] | None = None,
        use_accumulated_profile: bool = False,
        user_agent_override: str | None = None,
        block_webrtc: bool = False,
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

        ``login_flow`` (docs/REQUIREMENTS.md section 9 entry 15, Known
        Limitation #1: login/session): same best-effort contract as the
        other three parameters -- when given, a provider with a real,
        live browser page fills and submits the real login form
        (:class:`LoginFlow`'s own docstring has the full rationale,
        including why session persistence is demonstrated within one
        ``solve()`` call rather than across separate ones) before
        navigating to ``url`` itself; a provider that cannot support it
        (``ByparrProvider``) must not crash or silently drop it -- log a
        clear warning and solve ``url`` directly, unauthenticated, same
        as if ``login_flow`` had not been given at all.

        ``warm_session_urls`` (docs/REQUIREMENTS.md section 9 entry 21,
        Step 2): same best-effort contract as the other parameters --
        when given, a provider with a real, live browser page navigates
        to each URL, in order, within the *same* browser session (the
        same page/context ``url`` itself is ultimately solved with, not
        a separate one), before navigating to ``url``. This is the real
        fix for the gap :class:`LoginFlow`'s own docstring used to
        describe as permanent (see that docstring's own updated note):
        since every navigation here shares one real browser context,
        cookies/session state a warm-up page sets genuinely carry
        forward to ``url``'s own navigation, the way a real user
        browsing a real site would accumulate them. A provider that
        cannot support it (``ByparrProvider``) must not crash or
        silently drop it -- log a clear warning and solve ``url``
        directly, with no warm-up at all, same as if
        ``warm_session_urls`` had not been given.

        ``use_accumulated_profile`` (docs/REQUIREMENTS.md section 9
        entry 21, Step 2): same best-effort contract -- when ``True``, a
        provider with a real, live browser page starts this ``solve()``
        call's own browser context from a *cross-call* accumulated
        cookie/storage profile (built up over many real, separate
        ``solve()`` calls over real time -- see
        :mod:`src.providers.antibot.cookie_jar_manager`'s own module
        docstring for the full mechanism and its RRD-style retention),
        and saves this call's own resulting state back into that same
        accumulated profile on success. ``False`` (the default) keeps
        every existing caller's exact prior behavior: a genuinely fresh,
        empty browser profile every single call, the same complete
        isolation entry 17's own test suite depends on. A provider that
        cannot support it (``ByparrProvider``) must not crash or
        silently drop it -- log a clear warning and solve with a fresh
        profile regardless, same as if ``use_accumulated_profile`` were
        ``False``.

        ``user_agent_override`` (docs/REQUIREMENTS.md section 9 entry
        24/27): same best-effort contract as the other
        parameters -- when given, a provider with a real browser context
        to create (Camoufox/Patchright) sends this exact string as the
        browser's User-Agent for the whole ``solve()`` call instead of
        its own real default. ``ByparrProvider`` cannot support this at
        all -- confirmed by reading its own request payload model
        (``LinkRequest``), which has no ``userAgent``/``user_agent``
        field whatsoever (the same real, upstream FlareSolverr-protocol
        limitation, not something this project's own Byparr deployment
        chose to omit) -- so it must not crash or silently drop it: log
        a clear warning and solve with its own real, unmodified default
        User-Agent, same as if ``user_agent_override`` had not been
        given. ``None`` (the default) keeps every existing caller's
        exact prior behavior for every provider.

        ``block_webrtc`` (docs/PHASE_2_BACKLOG.md item 5, WebRTC Leak
        Prevention -- a real, confirmed gap: neither real-browser
        provider disables WebRTC by default, so a page's own JS can
        enumerate real local/network IP addresses via
        ``RTCPeerConnection`` ICE candidates regardless of every other
        anti-fingerprinting layer this project already has). Same
        best-effort contract as the other parameters -- when ``True``,
        a provider with a real browser-launch step it controls disables
        WebRTC entirely for the whole ``solve()`` call, not just the one
        page (``CamoufoxProvider``: a real, existing library capability,
        ``camoufox.utils.launch_options``'s own ``block_webrtc`` sets
        Firefox's ``media.peerconnection.enabled`` pref to ``False`` --
        the exact same mechanism this project already reads from that
        library's own source, confirmed directly, not assumed).
        ``PatchrightProvider``/``ByparrProvider`` cannot support this
        yet: Chromium has no equivalent context-creation-time option
        (a real gap would need an independent, launch-*flag*-based
        solution -- e.g. ``--force-webrtc-ip-handling-policy`` --
        deliberately out of this parameter's own scope for now, see
        docs/PHASE_2_BACKLOG.md item 5's own proposed design), and
        Byparr's ``/v1`` API has no browser-launch control at all (the
        same structural limitation ``user_agent_override`` already has
        for it) -- both must not crash or silently drop it: log a clear
        warning and solve with WebRTC left exactly as it already is.
        ``False`` (the default) keeps every existing caller's exact
        prior behavior for every provider.

        Implementations must raise
        :class:`src.core.exceptions.AntibotError` (never a bare
        ``Exception``) on failure, and must release any external resource
        they acquire (browser handle, HTTP connection, ...) in a
        ``finally`` block even when solving fails.
        """
        raise NotImplementedError
