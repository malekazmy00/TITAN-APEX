"""Periodic CSS class-name rotation for a curated subset of page elements.

Real sites rotate *some* markup (a CSS-modules-style hashed class on the
main content elements), not the whole page -- layout/nav classes usually
stay put. This does the same: only the "logical names" you register with
it rotate, on a configurable interval, and every rotated class is a fully
opaque token (no semantic hint), same shape as the styled-components
hashing that broke selector-based scraping on the real react-shopping-cart
SPA investigated in docs/REQUIREMENTS.md section 7 entry 5.

Stable `data-*` attributes are deliberately NOT handled here -- templates
that want a selector to survive rotation should key off `data-role`/
`data-testid` instead of a class name, exactly the "build selectors
smartly" lesson this challenge exists to test.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable

DEFAULT_INTERVAL_SECONDS = 15 * 60


class MarkupRandomizer:
    """Generates and periodically rotates opaque CSS classes for named elements."""

    def __init__(
        self,
        logical_names: list[str],
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        clock: Callable[[], float] | None = None,
        rng_seed: int | None = None,
    ) -> None:
        if not logical_names:
            raise ValueError("logical_names must be non-empty")
        if interval_seconds <= 0:
            raise ValueError(f"interval_seconds must be > 0, got {interval_seconds}")

        self._logical_names = list(logical_names)
        self._interval_seconds = interval_seconds
        self._clock = clock or time.monotonic
        self._rng = random.Random(rng_seed)
        self._last_rotation = self._clock()
        self._current_classes = self._generate_classes()

    def _generate_classes(self) -> dict[str, str]:
        return {name: f"x{self._rng.randrange(16**8):08x}" for name in self._logical_names}

    def _maybe_rotate(self) -> None:
        now = self._clock()
        if now - self._last_rotation >= self._interval_seconds:
            self._current_classes = self._generate_classes()
            self._last_rotation = now

    def get_class(self, logical_name: str) -> str:
        """Return the current opaque class for ``logical_name``, rotating first if due.

        Raises:
            KeyError: if ``logical_name`` was never registered.
        """
        if logical_name not in self._current_classes:
            raise KeyError(f"unregistered logical element name: {logical_name}")
        self._maybe_rotate()
        return self._current_classes[logical_name]
