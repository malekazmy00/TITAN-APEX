"""Unit tests for mock-target/structural/markup_randomizer.py.

Uses an injected clock (same pattern as src/middlewares/circuit_breaker.py)
so rotation timing is tested deterministically -- no real sleeping.
"""

from __future__ import annotations

import pytest
from structural.markup_randomizer import MarkupRandomizer


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def test_class_is_stable_within_the_interval() -> None:
    """Happy path: repeated calls before the interval elapses return the same class."""
    clock = _FakeClock()
    randomizer = MarkupRandomizer(["post-item"], interval_seconds=60, clock=clock, rng_seed=1)

    first = randomizer.get_class("post-item")
    clock.now += 30
    second = randomizer.get_class("post-item")

    assert first == second


def test_class_rotates_once_the_interval_elapses() -> None:
    clock = _FakeClock()
    randomizer = MarkupRandomizer(["post-item"], interval_seconds=60, clock=clock, rng_seed=1)

    first = randomizer.get_class("post-item")
    clock.now += 61
    second = randomizer.get_class("post-item")

    assert first != second


def test_only_registered_logical_names_rotate_others_are_untouched() -> None:
    """Only a curated subset of elements rotates -- confirms two independently
    registered names get independent (not identical) classes."""
    randomizer = MarkupRandomizer(["post-item", "post-author"], interval_seconds=60, rng_seed=1)

    assert randomizer.get_class("post-item") != randomizer.get_class("post-author")


def test_rejects_empty_logical_names() -> None:
    """Failure case 1: nothing to randomize is a misconfiguration, not a no-op."""
    with pytest.raises(ValueError, match="logical_names must be non-empty"):
        MarkupRandomizer([], interval_seconds=60)


def test_rejects_non_positive_interval() -> None:
    """Failure case 2: an interval of zero or less would mean 'rotate on every
    single request', defeating the point of a periodic rotation."""
    with pytest.raises(ValueError, match="interval_seconds must be > 0"):
        MarkupRandomizer(["post-item"], interval_seconds=0)


def test_get_class_rejects_unregistered_name() -> None:
    """Failure case 3: asking for a class that was never registered is a
    programming error in the caller (a template typo), not a silent None."""
    randomizer = MarkupRandomizer(["post-item"], interval_seconds=60)

    with pytest.raises(KeyError, match="unregistered logical element name"):
        randomizer.get_class("does-not-exist")
