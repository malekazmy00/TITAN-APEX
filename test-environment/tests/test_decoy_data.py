"""Unit tests for mock-target/structural/decoy_data.py."""

from __future__ import annotations

import pytest
from content_generator import generate_post
from structural.decoy_data import generate_decoy_twin


def test_decoy_shares_id_but_differs_in_content() -> None:
    """Happy path: the decoy is a genuine twin -- same post_id (so a
    selector-only-by-id scraper can't tell them apart), different text."""
    real = generate_post("session-a", 0)

    decoy = generate_decoy_twin(real, "session-a")

    assert decoy.post_id == real.post_id
    assert decoy.text != real.text
    assert decoy.comments == []


def test_decoy_is_deterministic_for_the_same_seed() -> None:
    real = generate_post("session-a", 0)

    first = generate_decoy_twin(real, "session-a")
    second = generate_decoy_twin(real, "session-a")

    assert first == second


def test_rejects_post_with_empty_id() -> None:
    """Failure case 1: a post with no id can't be twinned meaningfully."""
    real = generate_post("session-a", 0)
    real.post_id = ""

    with pytest.raises(ValueError, match="post_id must be non-empty"):
        generate_decoy_twin(real, "session-a")


def test_decoy_likes_never_exceeds_real_likes_when_real_likes_is_zero() -> None:
    """Failure-adjacent case 2: real_post.likes == 0 must not crash
    Faker's random_int(min=0, max=...) with an invalid (max < min) range."""
    real = generate_post("session-a", 0)
    real.likes = 0

    decoy = generate_decoy_twin(real, "session-a")

    assert decoy.likes == 0
