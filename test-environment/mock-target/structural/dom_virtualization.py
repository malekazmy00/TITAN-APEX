"""DOM Virtualization: ``/feed`` keeps only a bounded window of posts
genuinely present in the DOM at any one time -- new posts loading in
(client-side JS, appended as they arrive from ``/api/feed``) evict the
oldest ones once the rendered count exceeds the window, the same
mechanism a real virtualized list (react-window, a real social-feed
client) uses to keep DOM node count bounded regardless of how much total
content has actually been scrolled through.

Genuinely different in kind from every other structural challenge here:
honeypots/decoy-data/Shadow DOM are all about *what's reachable* in a
single, static DOM snapshot (a visibility check, an encapsulation
boundary) -- this is about *time*. An evicted post is not merely hidden
or hard to select: by the time anything (a raw HTML string, or a live
DOM query) reads the page, it is genuinely, unambiguously gone from the
DOM -- there is no selector or extraction strategy that recovers content
that no longer exists at read time. See docs/REQUIREMENTS.md section 9
entry 13 for the real, evidenced result of testing exactly that against
both of this project's own GenericSpider extraction modes.

The actual eviction happens client-side (``templates/feed.html``'s own
script, on every batch of newly-appended posts) -- this module documents
and unit-tests the exact rule that script mirrors by hand, the same
"the real logic lives in Python, tested here" shape
``structural/ab_variant.py``'s ``choose_variant``/``container_tag_for``
already have for their own (server-side) rendering decisions.
"""

from __future__ import annotations


def excess_count(rendered_count: int, window_size: int) -> int:
    """How many of the *oldest* rendered posts must be evicted right now
    to keep the DOM within ``window_size`` -- ``0`` when already within it.

    Raises:
        ValueError: if ``rendered_count`` is negative or ``window_size``
            is not positive -- neither is a real state to evict from.
    """
    if rendered_count < 0:
        raise ValueError(f"rendered_count must be >= 0, got {rendered_count}")
    if window_size <= 0:
        raise ValueError(f"window_size must be > 0, got {window_size}")
    return max(0, rendered_count - window_size)
