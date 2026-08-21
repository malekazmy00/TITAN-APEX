"""A/B structural variant selection -- same post content, a different
container element, chosen freshly on *every* request (not pinned to a
session).

Real A/B experiments are sometimes assigned per-request at an edge/CDN
layer before any session cookie is even read, so a scraper cannot safely
assume a selector that matched once will keep matching on the very next
request to the same URL
(docs/OBSTACLE_MAP_AND_ESCALATION_SCHEDULE.md's own framing: "نفس
endpoint، هيكل عشوائي بين طلبين متتاليين"). This module deliberately
varies something a selector-based scraper could plausibly hardcode --
the container *tag* -- while keeping every ``data-role``/``data-post-id``
attribute and the actual post content identical between variants, so the
only thing that legitimately breaks is an over-specific, tag-qualified
selector, never the content itself.
"""

from __future__ import annotations

import random
from collections.abc import Callable

VARIANTS = ("a", "b")
# variant "a": <article data-role="post"> -- the original container tag.
# variant "b": <div data-role="post"> -- same attributes/content, a
# different container element. `article[data-role="post"]` (this
# project's own mock_target*.yaml configs) matches variant "a" only; a
# plain `[data-role="post"]` attribute selector survives both.
_CONTAINER_TAGS = {"a": "article", "b": "div"}


def choose_variant(rand_fn: Callable[[], float] | None = None) -> str:
    """Pick a variant for a single request.

    ``rand_fn`` is an injectable zero-arg callable returning a float in
    [0, 1) (defaults to ``random.random``) -- deterministic in tests,
    genuinely random otherwise.
    """
    roll = (rand_fn or random.random)()
    return VARIANTS[0] if roll < 0.5 else VARIANTS[1]


def container_tag_for(variant: str) -> str:
    """The HTML container tag for ``variant``.

    Raises:
        KeyError: if ``variant`` isn't one of :data:`VARIANTS`.
    """
    if variant not in _CONTAINER_TAGS:
        raise KeyError(f"unknown A/B variant: {variant!r}")
    return _CONTAINER_TAGS[variant]
