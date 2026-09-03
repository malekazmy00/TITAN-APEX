"""Downloader middleware: renders JavaScript-heavy pages via a headless browser.

Only requests explicitly marked ``request.meta["playwright"] = True`` are
rendered this way (set by ``GenericSpider`` when a target's config sets
``render_js: true``) — every other request passes straight through
untouched. The renderer itself, and the function that runs it off the
Twisted reactor thread, are both injectable so unit tests never launch a
real browser or touch the network.

**Scroll diagnostics (docs/REQUIREMENTS.md section 9 entry 25 — a real,
CI-confirmed failure investigated on request, not guessed):** a real CI
run (33550885357) found that ``scrapingcourse.com``'s own infinite-scroll
page (the target ``_scroll_to_load_lazy_content`` below exists for)
yielded only its static first batch — the item count never grew past
what the page already has without any JS at all. Investigated for real
(the page's own served HTML, ``curl``'d directly, plus external sources on
this exact Playwright pattern — see that entry for citations): the site
triggers its next batch via an ``IntersectionObserver`` watching a
``#sentinel`` element, not a ``scroll`` event listener — and
``window.scrollTo()`` (what ``_scroll_to_load_lazy_content`` below does,
and has always done, since before this project's own separate,
independently-discovered ``page.mouse.wheel()`` fix in
``src/providers/antibot/_scroll.py`` for a *different* target/reason) is
documented, from multiple independent sources, as unreliable at
triggering an ``IntersectionObserver`` callback in headless Chromium —
unlike ``scrollIntoViewIfNeeded()`` or a real wheel/mouse input. That fix
is a real, separate follow-up (deliberately not applied in this same
change, matching this project's own "document the sub-gap, don't solve
everything in one pass" discipline — see entry 25 for the full trail).

What *is* added here, right now, is detection: every call to
``render_with_playwright`` now returns a :class:`ScrollDiagnostics`
snapshot (``attempts_used``, the page height before/after scrolling, and
how many real HTTP requests fired *during* the scroll loop), logged as a
structured line by :class:`PlaywrightMiddleware`. Without this, "did the
scroll trigger even fire" was a question only answerable by external
research after the fact, the same way this very investigation had to be
done from scratch; with it, a future CI run showing
``requests_during_scroll: 0`` is direct, in-the-log evidence the trigger
never fired at all (this failure mode), immediately distinguishable from
``requests_during_scroll: N, height unchanged`` (a different root cause —
requests fired but returned nothing new) or a genuinely finite page
(height simply stops growing on schedule, same as always). Purely
observational: it changes zero scrolling *behavior* for any existing
target, only what gets logged about it.

**The fix (docs/REQUIREMENTS.md section 9 entry 25's follow-up — real CI
evidence first showed the failure is flaky, not deterministic, so a
single green run was explicitly not treated as proof; see that entry
for the required multi-run confirmation before this is called
resolved):** ``_scroll_to_load_lazy_content`` no longer calls
``window.scrollTo()`` at all. It now drives scrolling with
``page.mouse.wheel()`` — a real, trusted input-level event, the exact
same fix this project already applied to
``src/providers/antibot/_scroll.py`` for a different target/reason
(entry 17's "Fourth revision") but had never ported here. A one-time
``page.mouse.move()`` runs first (``page.mouse.wheel()`` alone, with no
cursor ever positioned in the viewport, reliably produces zero real
scroll — the identical constraint ``_scroll.py`` documents for its own
callers). Deliberately *not* also ported: ``_scroll.py``'s randomized
delta/pause/fatigue model and its progressive collect-every-step
machinery — those solve a different problem (anti-detection realism,
and virtualized-list eviction) that this function's callers have never
needed; porting only the actual fix for *this* bug (an unreliable
trigger, not a detectable timing signature) keeps the change minimal
and the two modules' deliberate duplication (see this module's own
functions vs. ``_scroll.py``'s) honest about *why* they differ, not
just that they do.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from logging import Logger
from typing import Any, NamedTuple

from scrapy.http import HtmlResponse, Request, Response
from twisted.internet.defer import Deferred
from twisted.internet.threads import deferToThread

from src.core.exceptions import RenderError
from src.diagnostics.failure_registry import record_failure
from src.diagnostics.failure_taxonomy import FailureCategory, FailureRecord, ResolutionStatus
from src.logging_config import get_logger

DEFAULT_TIMEOUT_MS = 30_000
DEFAULT_MAX_SCROLL_ATTEMPTS = 8
DEFAULT_SCROLL_PAUSE_MS = 700


@dataclass(frozen=True, slots=True)
class ScrollDiagnostics:
    """Real, observed evidence from one ``_scroll_to_load_lazy_content`` run.

    ``requests_during_scroll`` is ``None`` when nothing was tracking real
    network requests during the scroll loop (a fake ``page`` in a unit
    test, or -- in principle -- a future caller that skips the listener
    for some reason) -- callers must not conflate "we didn't check" with
    "we checked and it was genuinely zero".
    """

    attempts_used: int
    initial_height: int
    final_height: int
    requests_during_scroll: int | None = None


class RenderedPage(NamedTuple):
    """Result of rendering one URL with a headless browser."""

    html: str
    status: int
    scroll_diagnostics: ScrollDiagnostics | None = None


# A precise Protocol would need to describe render_with_playwright's full
# keyword surface (executable_path, scroll tuning, ...) just to satisfy
# structural typing for the two calls that actually cross the
# PlaywrightMiddleware boundary (url, render_wait_ms, click_selector) --
# Callable[..., RenderedPage] says the same thing without that ceremony,
# and injected fakes in tests still get checked against RenderedPage.
Renderer = Callable[..., RenderedPage]
ThreadRunner = Callable[[Callable[[Request], Response], Request], Any]


def render_with_playwright(
    url: str,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    executable_path: str | None = None,
    max_scroll_attempts: int = DEFAULT_MAX_SCROLL_ATTEMPTS,
    scroll_pause_ms: int = DEFAULT_SCROLL_PAUSE_MS,
    render_wait_ms: int | None = None,
    click_selector: str | None = None,
) -> RenderedPage:
    """Default renderer: launches a real headless Chromium via Playwright.

    After the initial navigation:

    1. if ``click_selector`` is set, that element is clicked (Playwright
       scrolls it into view first) -- for content that only appears after
       an explicit interaction, e.g. a "Load More" button that a scroll
       never triggers (docs/REQUIREMENTS.md, section 7, entry 4);
    2. the page is scrolled to the bottom repeatedly (up to
       ``max_scroll_attempts`` times, pausing ``scroll_pause_ms`` between
       attempts) and stops early once the page stops growing -- this is
       what makes infinite-scroll targets actually pull in more than the
       first batch. Harmless (a couple of no-op scrolls) on a page that
       doesn't use infinite scroll;
    3. if ``render_wait_ms`` is set, one more fixed wait -- for a site
       that adds its own client-side delay *after* content has already
       arrived and before it renders, which neither of the above is
       guaranteed to cover (docs/REQUIREMENTS.md, section 7, entry 3).

    Must be called off the Twisted reactor thread (Playwright's sync API
    cannot share a thread with an already-running event loop) — see
    :func:`_default_thread_runner`.

    ``executable_path`` lets a deployment point at a specific Chromium
    binary (e.g. when the installed browser revision doesn't match what
    this Playwright version expects by default) via the
    ``TITAN_PLAYWRIGHT_EXECUTABLE`` setting — never hardcoded here.
    """
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(executable_path=executable_path)
        except PlaywrightError as exc:
            raise RenderError(f"playwright failed to launch chromium for {url}") from exc

        try:
            page = browser.new_page()
            try:
                response = page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                if click_selector:
                    page.click(click_selector, timeout=timeout_ms)

                request_count = 0

                def _count_request(_request: Any) -> None:
                    nonlocal request_count
                    request_count += 1

                page.on("request", _count_request)
                try:
                    scroll_result = _scroll_to_load_lazy_content(
                        page, max_scroll_attempts, scroll_pause_ms
                    )
                finally:
                    page.remove_listener("request", _count_request)
                diagnostics = ScrollDiagnostics(
                    attempts_used=scroll_result.attempts_used,
                    initial_height=scroll_result.initial_height,
                    final_height=scroll_result.final_height,
                    requests_during_scroll=request_count,
                )

                if render_wait_ms:
                    page.wait_for_timeout(render_wait_ms)
                html = page.content()
                status = response.status if response is not None else 200
                return RenderedPage(html=html, status=status, scroll_diagnostics=diagnostics)
            except PlaywrightError as exc:
                raise RenderError(f"playwright failed to render {url}") from exc
            finally:
                page.close()
        finally:
            browser.close()


#: Wheel-scroll delta, in pixels, for each attempt inside
#: ``_scroll_to_load_lazy_content`` -- a fixed, generous jump (this
#: function's own contract has never needed per-step randomization,
#: unlike ``src/providers/antibot/_scroll.py``'s
#: ``DEFAULT_SCROLL_DELTA_RANGE_PX``, which exists for a different
#: reason -- see this module's own docstring). Large enough to clear a
#: real page's "near the bottom" trigger threshold in one attempt on
#: every already-proven lazy-load target this function serves.
_SCROLL_WHEEL_DELTA_PX = 2500.0


def _scroll_to_load_lazy_content(
    page: Any, max_attempts: int, pause_ms: int
) -> ScrollDiagnostics:
    """Scroll to the bottom of ``page`` until its height stops growing.

    ``page`` is a ``playwright.sync_api.Page``, typed as ``Any`` here
    since Playwright ships without inline type stubs.

    Drives scrolling with a real ``page.mouse.wheel()`` input event, not
    ``window.scrollTo()`` -- see this module's own docstring's "The fix"
    paragraph for why. A one-time ``page.mouse.move()`` runs first so the
    (virtual) cursor is actually positioned somewhere in the viewport
    before the first wheel event -- ``page.mouse.wheel()`` alone, with no
    prior ``move()``, reliably produces zero real scroll (the same
    constraint ``src/providers/antibot/_scroll.py`` documents for its own
    callers).

    Returns a :class:`ScrollDiagnostics` snapshot (``requests_during_scroll``
    left ``None`` here — this function has no network visibility of its
    own; ``render_with_playwright`` fills that field in from its own
    request listener, wrapped around this call, since only the caller
    that owns the real ``page.on("request", ...)`` registration can count
    them honestly). See this module's own docstring for why this exists.
    """
    initial_height = page.evaluate("document.body.scrollHeight")
    previous_height = initial_height
    final_height = initial_height
    attempts_used = 0
    page.mouse.move(200, 200)
    for _ in range(max_attempts):
        page.mouse.wheel(0, _SCROLL_WHEEL_DELTA_PX)
        page.wait_for_timeout(pause_ms)
        current_height = page.evaluate("document.body.scrollHeight")
        attempts_used += 1
        final_height = current_height
        if current_height <= previous_height:
            break
        previous_height = current_height
    return ScrollDiagnostics(
        attempts_used=attempts_used, initial_height=initial_height, final_height=final_height
    )


def _default_thread_runner(
    render_fn: Callable[[Request], Response], request: Request
) -> Deferred[Response]:
    return deferToThread(render_fn, request)


class PlaywrightMiddleware:
    """Renders ``request.meta["playwright"]``-flagged requests with a headless browser."""

    def __init__(
        self,
        renderer: Renderer | None = None,
        logger: Logger | None = None,
        thread_runner: ThreadRunner | None = None,
    ) -> None:
        self.renderer = renderer or render_with_playwright
        self.logger = logger or get_logger(__name__)
        self._thread_runner = thread_runner or _default_thread_runner

    @classmethod
    def from_crawler(cls, crawler: Any) -> PlaywrightMiddleware:
        executable_path = crawler.settings.get("TITAN_PLAYWRIGHT_EXECUTABLE") or None
        if executable_path:
            renderer: Renderer = functools.partial(
                render_with_playwright, executable_path=executable_path
            )
            return cls(renderer=renderer)
        return cls()

    def process_request(self, request: Request, spider: Any) -> Any:
        if not request.meta.get("playwright"):
            return None
        return self._thread_runner(self._render, request)

    def _render(self, request: Request) -> Response:
        try:
            page = self.renderer(
                request.url,
                render_wait_ms=request.meta.get("render_wait_ms"),
                click_selector=request.meta.get("click_selector"),
            )
        except RenderError as exc:
            self.logger.error("playwright_middleware.render_failed", extra={"url": request.url})
            # Unified failure taxonomy (docs/REQUIREMENTS.md section 9
            # entry 28): render_with_playwright wraps Playwright's own
            # launch/navigation errors here -- an environment/browser
            # failure, never a target's own defense (PlaywrightMiddleware
            # has no anti-bot-solving logic at all).
            record_failure(
                FailureRecord(
                    timestamp=datetime.now(tz=UTC),
                    target=request.url,
                    provider="playwright",
                    failure_category=FailureCategory.NETWORK_INFRA_TRANSIENT,
                    raw_signal={"reason": str(exc)},
                    source="playwright_middleware.render_failed",
                )
            )
            raise
        if page.scroll_diagnostics is not None:
            diagnostics = page.scroll_diagnostics
            # Real, always-on evidence for the exact failure mode
            # docs/REQUIREMENTS.md section 9 entry 25 investigated by hand
            # (requests_during_scroll: 0 despite attempts_used == the
            # configured max is direct, in-the-log proof the scroll
            # trigger never fired at all -- no more guessing from a bare
            # item count after the fact). See this module's own docstring.
            self.logger.info(
                "playwright_middleware.scroll_diagnostics",
                extra={
                    "url": request.url,
                    "attempts_used": diagnostics.attempts_used,
                    "initial_height": diagnostics.initial_height,
                    "final_height": diagnostics.final_height,
                    "requests_during_scroll": diagnostics.requests_during_scroll,
                },
            )
            if diagnostics.requests_during_scroll == 0:
                # docs/REQUIREMENTS.md section 9 entry 29 ("الطبقة 2"):
                # a real, CI-observed false positive caught in entry
                # 28's own CI-confirmation run -- requests_during_scroll
                # == 0 alone cannot distinguish "the scroll trigger
                # should have loaded more content but didn't" (a real
                # timing race) from "this page never had scroll-
                # triggered content to load in the first place" (page
                # height never changed even once -- there was nothing
                # to race against). initial_height == final_height is
                # direct, in-the-log proof of the second case: real
                # infinite-scroll pages this project has confirmed the
                # fix against (entry 27's webscraper.io/test-sites/scroll)
                # always show final_height > initial_height once the
                # first successful batch loads, scroll bug or not.
                if diagnostics.initial_height == diagnostics.final_height:
                    record_failure(
                        FailureRecord(
                            timestamp=datetime.now(tz=UTC),
                            target=request.url,
                            provider="playwright",
                            failure_category=FailureCategory.NO_SCROLLABLE_CONTENT,
                            raw_signal={
                                "attempts_used": diagnostics.attempts_used,
                                "initial_height": diagnostics.initial_height,
                                "final_height": diagnostics.final_height,
                                "requests_during_scroll": diagnostics.requests_during_scroll,
                            },
                            resolution_status=ResolutionStatus.RESOLVED,
                            source="playwright_middleware.scroll_diagnostics",
                        )
                    )
                else:
                    # Unified failure taxonomy (docs/REQUIREMENTS.md
                    # section 9 entry 28): the exact signal entry 25
                    # investigated and entry 27's mouse.wheel() fix
                    # targeted -- the scroll trigger genuinely never
                    # fired a single request, on a page that (unlike
                    # the branch above) does have real scroll-triggered
                    # content, since its height did change.
                    # resolution_status is RESOLVED (not unresolved)
                    # because a real, CI-confirmed fix exists for the
                    # class of failure this represents (entry 27,
                    # confirmed 4/4 independent CI runs); an individual
                    # occurrence still gets recorded since the
                    # underlying mechanism remains a real, if rare, race.
                    record_failure(
                        FailureRecord(
                            timestamp=datetime.now(tz=UTC),
                            target=request.url,
                            provider="playwright",
                            failure_category=FailureCategory.TIMING_RACE,
                            raw_signal={
                                "attempts_used": diagnostics.attempts_used,
                                "initial_height": diagnostics.initial_height,
                                "final_height": diagnostics.final_height,
                                "requests_during_scroll": diagnostics.requests_during_scroll,
                            },
                            resolution_status=ResolutionStatus.RESOLVED,
                            source="playwright_middleware.scroll_diagnostics",
                        )
                    )
        return HtmlResponse(
            url=request.url, body=page.html.encode("utf-8"), status=page.status, request=request
        )
