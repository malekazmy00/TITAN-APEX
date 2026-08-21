"""The Level-4-of-this-environment structural challenge: a social-feed-shaped
GraphQL-ish nested API, real lazy loading, per-session content variance,
and escalating rate limiting -- the four traits docs/REQUIREMENTS.md
section 8's Escalation Cycle calls out as the "biggest challenge" here,
genuinely different from every static/paginated target built so far.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from content_generator import Post, generate_feed_page

# Bounded so a real crawl terminates -- an infinite feed would never let
# next_page-following stop, which isn't the point of this challenge.
MAX_FEED_PAGES = 5


@dataclass
class FeedPage:
    posts: list[Post]
    end_cursor: str
    has_next_page: bool


def build_feed_page(seed: str, after_cursor: str | None, page_size: int) -> FeedPage:
    """Build one page of the nested feed, GraphQL-cursor style.

    Raises:
        ValueError: if ``after_cursor`` is set but isn't a valid page number,
            or if ``page_size`` is not positive.
    """
    if page_size <= 0:
        raise ValueError(f"page_size must be > 0, got {page_size}")

    if after_cursor is None:
        page = 0
    else:
        try:
            page = int(after_cursor) + 1
        except ValueError as exc:
            raise ValueError(f"after_cursor is not a valid page cursor: {after_cursor!r}") from exc

    posts = generate_feed_page(seed, page, page_size)
    return FeedPage(
        posts=posts,
        end_cursor=str(page),
        has_next_page=page < MAX_FEED_PAGES - 1,
    )


@dataclass
class RateLimitResult:
    allowed: bool
    retry_after_seconds: int


class FeedRateLimiter:
    """Sliding-window limiter with an *escalating* Retry-After on repeat abuse.

    Real social platforms don't hard-block after a threshold -- they slow
    you down more and more, which is what ``retry_after_seconds`` growing
    with each consecutive violation models.
    """

    def __init__(
        self,
        threshold: int,
        window_seconds: int,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if threshold <= 0:
            raise ValueError(f"threshold must be > 0, got {threshold}")
        if window_seconds <= 0:
            raise ValueError(f"window_seconds must be > 0, got {window_seconds}")

        self._threshold = threshold
        self._window_seconds = window_seconds
        self._clock = clock or time.monotonic
        self._request_times: dict[str, list[float]] = {}
        self._violations: dict[str, int] = {}

    def check(self, client_key: str) -> RateLimitResult:
        """Record one request for ``client_key`` and decide allow/deny.

        Raises:
            ValueError: if ``client_key`` is empty -- there's no client to
                rate-limit.
        """
        if not client_key:
            raise ValueError("client_key must be non-empty")

        now = self._clock()
        window_start = now - self._window_seconds
        history = self._request_times.setdefault(client_key, [])
        history[:] = [t for t in history if t >= window_start]
        history.append(now)

        if len(history) <= self._threshold:
            self._violations.pop(client_key, None)
            return RateLimitResult(allowed=True, retry_after_seconds=0)

        violations = self._violations.get(client_key, 0) + 1
        self._violations[client_key] = violations
        return RateLimitResult(
            allowed=False, retry_after_seconds=self._window_seconds * violations
        )
