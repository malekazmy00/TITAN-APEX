"""Full-screen interstitial overlay: real content stays genuinely present
in the DOM underneath it, never removed or replaced -- what makes this a
different problem from Shadow DOM (content isolated from a plain DOM
read, structural/shadow_dom.py) or DOM Virtualization (content genuinely
evicted from the DOM over time, structural/dom_virtualization.py) is that
here the content is never hidden or removed at all. The obstacle is a
real, interactive element that blocks *further progress* until it's
dismissed -- the same class of problem docs/OBSTACLE_MAP_AND_ESCALATION_
SCHEDULE.md's cookie-consent-wall round already solved with
``click_selector`` (structural/cookie_wall.py's own docstring), just
triggered later (by a delay or a scroll threshold) instead of gating the
very first response.

Deliberately a client-side-only obstacle -- unlike cookie_wall.py's
server-side content gate, the real posts here are always present in the
initial/streamed HTML; only *loading more of them* (via the page's own
``loadMore()``, see ``templates/feed_interstitial.html``) is what the
overlay blocks while it's shown. That's a JS-level gate (a flag the
page's own fetch loop checks), not CSS ``overflow: hidden`` on the body
-- ``src.providers.antibot._scroll``'s own module docstring documents
that this stack's scroll simulation dispatches a synthetic ``'scroll'``
Event unconditionally, regardless of whether the browser's real scroll
position could move at all, so a CSS-only block would not reliably stop
it the way a real site's own JS gate does.
"""

from __future__ import annotations

from dataclasses import dataclass

from content_generator import Post, generate_feed_page

VALID_TRIGGERS = {"time", "scroll"}
INTERSTITIAL_CLOSE_SELECTOR = '[data-role="interstitial-close"]'
INTERSTITIAL_SELECTOR = '[data-role="interstitial"]'


@dataclass
class InterstitialFeedPage:
    posts: list[Post]
    end_cursor: str
    has_next_page: bool


def build_interstitial_feed_page(
    seed: str, after_cursor: str | None, page_size: int, total_batches: int
) -> InterstitialFeedPage:
    """One cursor-paginated batch of ``/feed-interstitial``'s own feed.

    Deliberately independent of ``structural/feed.py``'s
    ``build_feed_page`` (used by ``/api/feed``) -- this route's own
    ``total_batches`` bound (not ``MAX_FEED_PAGES``) and no shared
    ``FeedRateLimiter`` state, so this layer's own tests can't be
    affected by however many requests other, unrelated live tests have
    already made against ``/api/feed`` in the same CI run.

    Raises:
        ValueError: if ``after_cursor`` is set but isn't a valid page
            number, ``page_size`` isn't positive, or ``total_batches``
            isn't positive.
    """
    if page_size <= 0:
        raise ValueError(f"page_size must be > 0, got {page_size}")
    if total_batches <= 0:
        raise ValueError(f"total_batches must be > 0, got {total_batches}")

    if after_cursor is None:
        page = 0
    else:
        try:
            page = int(after_cursor) + 1
        except ValueError as exc:
            raise ValueError(f"after_cursor is not a valid page cursor: {after_cursor!r}") from exc

    posts = generate_feed_page(seed, page, page_size)
    return InterstitialFeedPage(
        posts=posts,
        end_cursor=str(page),
        has_next_page=page < total_batches - 1,
    )


def render_interstitial_script(trigger: str, delay_ms: int, scroll_percent: int) -> str:
    """Inline JS: shows the overlay per ``trigger`` -- ``"time"`` after
    ``delay_ms`` milliseconds, ``"scroll"`` once the page has been
    scrolled past ``scroll_percent`` percent of its scrollable height --
    and sets ``window.__interstitialShown = True`` while it's up.
    ``templates/feed_interstitial.html``'s own ``loadMore()`` checks that
    flag before fetching another batch, so the overlay genuinely blocks
    further loading, not just the view. A close-button click
    (``INTERSTITIAL_CLOSE_SELECTOR``) clears the flag and hides the
    overlay again -- the same one-click dismissal
    ``click_selector`` already drives for the cookie-consent wall.

    Raises:
        ValueError: if ``trigger`` isn't ``"time"``/``"scroll"``,
            ``delay_ms`` isn't positive, or ``scroll_percent`` isn't in
            ``(0, 100]`` -- all meaningless configurations.
    """
    if trigger not in VALID_TRIGGERS:
        raise ValueError(f"trigger must be one of {sorted(VALID_TRIGGERS)}, got {trigger!r}")
    if delay_ms <= 0:
        raise ValueError(f"delay_ms must be > 0, got {delay_ms}")
    if not (0 < scroll_percent <= 100):
        raise ValueError(f"scroll_percent must be in (0, 100], got {scroll_percent}")

    wiring = (
        # docs/REQUIREMENTS.md section 9 entry 17's feed_interstitial.html
        # follow-up: a real, known-window signal *other* client-side
        # code on this same page (templates/feed_interstitial.html's own
        # maybeLoadMoreIfNoScrollRoom()) can read to answer "might an
        # interstitial still appear soon" *before* window.__interstitialShown
        # itself ever becomes true -- set synchronously here, before the
        # trigger below is even armed, so it's always available the
        # moment any other script tag on the page runs after this one
        # (document order guarantees that; the setTimeout/scroll-listener
        # below only *fires* later, asynchronously). Exposing the
        # trigger's own configured shape, not just a bare boolean,
        # deliberately lets a reader distinguish "definitely armed for a
        # known window" (trigger == 'time') from anything this specific
        # helper doesn't have a precise window for.
        f"window.__interstitialTrigger = {trigger!r};"
        "window.__interstitialArmedAt = Date.now();"
        f"window.__interstitialDelayMs = {delay_ms if trigger == 'time' else 'null'};"
        "function showInterstitial() {"
        "if (window.__interstitialShown) { return; }"
        "window.__interstitialShown = true;"
        f"document.querySelector('{INTERSTITIAL_SELECTOR}').style.display = 'flex';"
        "}"
        f"document.querySelector('{INTERSTITIAL_CLOSE_SELECTOR}').addEventListener("
        "'click', function () {"
        "window.__interstitialShown = false;"
        f"document.querySelector('{INTERSTITIAL_SELECTOR}').style.display = 'none';"
        "});"
    )

    if trigger == "time":
        gate = f"setTimeout(showInterstitial, {delay_ms});"
    else:
        gate = (
            "window.addEventListener('scroll', function () {"
            "var scrollable = document.body.scrollHeight - window.innerHeight;"
            "var pct = scrollable > 0 ? (window.scrollY / scrollable) * 100 : 100;"
            f"if (pct >= {scroll_percent}) {{ showInterstitial(); }}"
            "});"
        )

    return wiring + gate
