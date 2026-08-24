"""Unit tests for mock-target/structural/dom_virtualization.py."""

from __future__ import annotations

import pytest
from structural.dom_virtualization import excess_count


def test_no_eviction_needed_when_within_the_window() -> None:
    """Happy path: fewer rendered posts than the window -- nothing to evict."""
    assert excess_count(rendered_count=3, window_size=5) == 0


def test_no_eviction_needed_when_exactly_at_the_window() -> None:
    assert excess_count(rendered_count=5, window_size=5) == 0


def test_excess_is_the_difference_over_the_window() -> None:
    """Happy path: 10 rendered posts, window of 5 -- evict the oldest 5."""
    assert excess_count(rendered_count=10, window_size=5) == 5


def test_rejects_negative_rendered_count() -> None:
    """Failure case 1: there's no such thing as -1 rendered posts."""
    with pytest.raises(ValueError, match="rendered_count must be >= 0"):
        excess_count(rendered_count=-1, window_size=5)


def test_rejects_non_positive_window_size() -> None:
    """Failure case 2: a window of 0 (or negative) posts is meaningless --
    nothing could ever stay rendered at all."""
    with pytest.raises(ValueError, match="window_size must be > 0"):
        excess_count(rendered_count=10, window_size=0)


def test_zero_rendered_count_is_allowed() -> None:
    """Happy path (empty case): nothing rendered yet is a real, valid
    starting state, not an error."""
    assert excess_count(rendered_count=0, window_size=5) == 0
