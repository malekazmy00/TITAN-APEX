"""Unit tests for mock-target/content_generator.py."""

from __future__ import annotations

import pytest
from content_generator import (
    generate_catalog,
    generate_comment,
    generate_feed_page,
    generate_post,
    generate_product,
)


def test_generate_post_is_deterministic_for_the_same_seed_and_index() -> None:
    """Happy path: same (seed, index) always yields the same fake post."""
    first = generate_post("session-a", 3)
    second = generate_post("session-a", 3)

    assert first == second
    assert first.post_id == "session-a-post-3"
    assert first.author
    assert first.text
    assert 0 <= first.likes <= 5000


def test_different_seeds_yield_different_content() -> None:
    """Different sessions must see different content -- the whole point of the
    per-session feed-variance challenge."""
    a = generate_post("session-a", 0)
    b = generate_post("session-b", 0)

    assert a.text != b.text or a.author != b.author


def test_generate_post_rejects_negative_index() -> None:
    """Failure case 1: a negative index is meaningless."""
    with pytest.raises(ValueError, match="index must be >= 0"):
        generate_post("session-a", -1)


def test_generate_comment_rejects_negative_depth() -> None:
    """Failure case 2: a negative nesting depth is meaningless."""
    with pytest.raises(ValueError, match="depth must be >= 0"):
        generate_comment("session-a", 0, 0, depth=-1)


def test_generate_feed_page_rejects_non_positive_page_size() -> None:
    """Failure case 3: a zero/negative page size can't produce a page."""
    with pytest.raises(ValueError, match="page_size must be > 0"):
        generate_feed_page("session-a", page=0, page_size=0)


def test_generate_feed_page_rejects_negative_page() -> None:
    """Failure case 4: there is no such thing as a negative page number."""
    with pytest.raises(ValueError, match="page must be >= 0"):
        generate_feed_page("session-a", page=-1, page_size=5)


def test_generate_feed_page_returns_requested_count_with_comments() -> None:
    posts = generate_feed_page("session-a", page=0, page_size=5, comments_per_post=2)

    assert len(posts) == 5
    for post in posts:
        assert len(post.comments) == 2


def test_generate_feed_page_pages_do_not_overlap() -> None:
    page0 = generate_feed_page("session-a", page=0, page_size=3)
    page1 = generate_feed_page("session-a", page=1, page_size=3)

    ids_page0 = {p.post_id for p in page0}
    ids_page1 = {p.post_id for p in page1}
    assert ids_page0.isdisjoint(ids_page1)


# --- generate_product / generate_catalog (docs/REQUIREMENTS.md section 9
# entry 23, /spa-catalog) ----------------------------------------------


def test_generate_product_is_deterministic_for_the_same_seed_and_index() -> None:
    """Happy path: same (seed, index) always yields the same fake product."""
    first = generate_product("session-a", 3)
    second = generate_product("session-a", 3)

    assert first == second
    assert first.product_id == "session-a-product-3"
    assert first.title
    assert first.price > 0
    assert first.image_url


def test_generate_product_different_seeds_yield_different_content() -> None:
    a = generate_product("session-a", 0)
    b = generate_product("session-b", 0)

    assert a.title != b.title or a.price != b.price


def test_generate_product_rejects_negative_index() -> None:
    """Failure case 1: a negative index is meaningless."""
    with pytest.raises(ValueError, match="index must be >= 0"):
        generate_product("session-a", -1)


def test_generate_catalog_returns_requested_count() -> None:
    products = generate_catalog("session-a", 8)

    assert len(products) == 8
    assert len({p.product_id for p in products}) == 8  # all unique


def test_generate_catalog_rejects_non_positive_count() -> None:
    """Failure case 2: a zero/negative count can't produce a catalog."""
    with pytest.raises(ValueError, match="count must be > 0"):
        generate_catalog("session-a", 0)
