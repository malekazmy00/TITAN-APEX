"""Unit tests for mock-target/structural/ab_variant.py."""

from __future__ import annotations

import pytest
from structural.ab_variant import choose_variant, container_tag_for


def test_choose_variant_a_below_the_midpoint() -> None:
    """Happy path: a low roll picks variant 'a' (the original <article> tag)."""
    assert choose_variant(rand_fn=lambda: 0.0) == "a"


def test_choose_variant_b_at_or_above_the_midpoint() -> None:
    """Happy path: a high roll picks variant 'b' (the <div> container)."""
    assert choose_variant(rand_fn=lambda: 0.99) == "b"


def test_container_tag_for_each_real_variant() -> None:
    assert container_tag_for("a") == "article"
    assert container_tag_for("b") == "div"


def test_container_tag_for_rejects_an_unknown_variant() -> None:
    """Failure case 1: an unrecognized variant name is a real bug, not
    silently defaulted to either tag."""
    with pytest.raises(KeyError, match="unknown A/B variant"):
        container_tag_for("c")


def test_default_rand_fn_produces_both_variants_over_many_calls() -> None:
    """Failure-adjacent case 2: without an injected rand_fn, real
    randomness must actually vary -- not a fixed/predictable default that
    would defeat the point of testing "does structure change between
    requests" at all."""
    variants = {choose_variant() for _ in range(50)}
    assert variants == {"a", "b"}
