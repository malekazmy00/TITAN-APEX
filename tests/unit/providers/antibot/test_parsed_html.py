"""Unit tests for src/providers/antibot/_parsed_html.py."""

from __future__ import annotations

import pytest

from src.providers.antibot.parsed_html import (
    extract_parsed_html_items,
    extract_positional_html_items,
)

SAMPLE_HTML = """
<html><body>
  <article data-role="post" data-post-id="p1">
    <span data-role="post-author">alice</span>
    <p data-role="post-text">hi</p>
  </article>
  <article data-role="post" data-post-id="p2">
    <span data-role="post-author">bob</span>
    <p data-role="post-text">yo</p>
  </article>
</body></html>
"""

FIELD_SELECTORS = {
    "post_id": "::attr(data-post-id)",
    "author": '[data-role="post-author"]::text',
    "text": '[data-role="post-text"]::text',
}


def test_extracts_one_item_per_matching_row() -> None:
    """Happy path: mirrors generic_spider.py's own _parse_html output
    shape exactly for the same HTML/selectors."""
    items = extract_parsed_html_items(SAMPLE_HTML, '[data-role="post"]', FIELD_SELECTORS)

    assert items == [
        {"post_id": "p1", "author": "alice", "text": "hi"},
        {"post_id": "p2", "author": "bob", "text": "yo"},
    ]


def test_no_matching_rows_yields_an_empty_list() -> None:
    """Happy path (empty case): zero matches is a real, valid result."""
    items = extract_parsed_html_items("<html></html>", '[data-role="post"]', FIELD_SELECTORS)

    assert items == []


def test_missing_field_resolves_to_none() -> None:
    """Failure-adjacent case 1: a field selector matching nothing within a
    row resolves to None -- same shape response.css(...).getall()
    returning [] already produces."""
    html = '<article data-role="post" data-post-id="p1"></article>'

    items = extract_parsed_html_items(
        html, '[data-role="post"]', {"author": '[data-role="post-author"]::text'}
    )

    assert items == [{"author": None}]


def test_multiple_matches_for_one_field_yield_a_list() -> None:
    """Failure-adjacent case 2: a field selector matching more than one
    descendant returns every match as a list."""
    html = (
        '<article data-role="post"><span class="tag">a</span>'
        '<span class="tag">b</span></article>'
    )

    items = extract_parsed_html_items(html, '[data-role="post"]', {"tags": "span.tag::text"})

    assert items == [{"tags": ["a", "b"]}]


def test_rejects_an_empty_item_selector() -> None:
    """Failure case 3: nothing meaningful to query with an empty selector."""
    with pytest.raises(ValueError, match="item_selector must be non-empty"):
        extract_parsed_html_items(SAMPLE_HTML, "", FIELD_SELECTORS)


# --- extract_positional_html_items (docs/REQUIREMENTS.md section 9 entry
# 23, Known Limitation #5's real fix) --------------------------------------

# Deliberately no per-item wrapper and no stable class/attribute anywhere
# (a real react-shopping-cart / styled-components-shaped page: opaque,
# hashed classes only, siblings not descendants) -- the exact shape
# extract_parsed_html_items/extract_live_dom_items cannot express at all.
SPA_HTML = """
<html><body>
  <main id="catalog">
    <img class="x1a1a1a1" src="/img/1.png" alt="Widget">
    <h3 class="x2b2b2b2">Widget</h3>
    <span class="x3c3c3c3">$9.99</span>
    <img class="x1a1a1a1" src="/img/2.png" alt="Gadget">
    <h3 class="x2b2b2b2">Gadget</h3>
    <span class="x3c3c3c3">$19.99</span>
  </main>
</body></html>
"""
SPA_SLOT_SELECTOR = "#catalog > *"
SPA_FIELD_SELECTORS = {
    "image_url": "0::attr(src)",
    "title": "1::text",
    "price": "2::text",
}


def test_positional_extracts_one_item_per_group() -> None:
    """Happy path: 6 flat slot elements, group_size=3 -> 2 items, fields
    read by position, not by any class/attribute selector."""
    items = extract_positional_html_items(SPA_HTML, SPA_SLOT_SELECTOR, 3, SPA_FIELD_SELECTORS)

    assert items == [
        {"image_url": "/img/1.png", "title": "Widget", "price": "$9.99"},
        {"image_url": "/img/2.png", "title": "Gadget", "price": "$19.99"},
    ]


def test_positional_no_matching_slots_yields_an_empty_list() -> None:
    """Happy path (empty case): zero matches is a real, valid result."""
    items = extract_positional_html_items(
        "<html></html>", SPA_SLOT_SELECTOR, 3, SPA_FIELD_SELECTORS
    )

    assert items == []


def test_positional_drops_an_incomplete_trailing_group() -> None:
    """Failure-adjacent case 1: stray extra slots that don't form a whole
    group are silently dropped -- there's no well-defined field value for
    a partial group, for any of its members."""
    html = SPA_HTML.replace("</main>", '<img class="x1a1a1a1" src="/img/3.png"></main>')

    items = extract_positional_html_items(html, SPA_SLOT_SELECTOR, 3, SPA_FIELD_SELECTORS)

    assert len(items) == 2  # the 7th slot alone never becomes a 3rd item


def test_positional_missing_offset_resolves_to_none() -> None:
    """Failure-adjacent case 2: an attribute absent on that particular
    slot resolves to None -- same "not found" shape every other
    extraction path here already has."""
    html = "<main id='catalog'><img src='/img/1.png'><h3>Widget</h3></main>"

    items = extract_positional_html_items(
        html, SPA_SLOT_SELECTOR, 2, {"alt_text": "0::attr(alt)"}
    )

    assert items == [{"alt_text": None}]


def test_positional_rejects_an_empty_slot_selector() -> None:
    """Failure case 3a: nothing meaningful to query with an empty selector."""
    with pytest.raises(ValueError, match="slot_selector must be non-empty"):
        extract_positional_html_items(SPA_HTML, "", 3, SPA_FIELD_SELECTORS)


def test_positional_rejects_a_non_positive_group_size() -> None:
    """Failure case 3b: a zero/negative group size is meaningless."""
    with pytest.raises(ValueError, match="group_size must be > 0"):
        extract_positional_html_items(SPA_HTML, SPA_SLOT_SELECTOR, 0, SPA_FIELD_SELECTORS)


def test_positional_rejects_a_malformed_field_expression() -> None:
    """Failure case 3c: an expression that isn't {int}::text/{int}::attr(name)."""
    with pytest.raises(ValueError, match="unsupported positional field expression"):
        extract_positional_html_items(SPA_HTML, SPA_SLOT_SELECTOR, 3, {"title": "not-an-offset"})


def test_positional_rejects_a_negative_offset() -> None:
    with pytest.raises(ValueError, match="unsupported positional field expression"):
        extract_positional_html_items(SPA_HTML, SPA_SLOT_SELECTOR, 3, {"title": "-1::text"})


def test_positional_rejects_a_non_numeric_offset() -> None:
    """Distinct from test_positional_rejects_a_malformed_field_expression:
    that one has no '::' separator at all; this one has a real separator
    but a non-integer offset part (e.g. a leftover CSS-descendant-style
    prefix, the *other* extraction strategy's syntax used by mistake)."""
    with pytest.raises(ValueError, match="unsupported positional field expression"):
        extract_positional_html_items(SPA_HTML, SPA_SLOT_SELECTOR, 3, {"title": "abc::text"})


def test_positional_rejects_an_unsupported_kind() -> None:
    """A real '::' separator and a valid integer offset, but a kind that
    is neither 'text' nor 'attr(name)'."""
    with pytest.raises(ValueError, match="unsupported positional field expression"):
        extract_positional_html_items(SPA_HTML, SPA_SLOT_SELECTOR, 3, {"title": "0::foo"})


def test_positional_rejects_an_offset_outside_the_group() -> None:
    """A field whose own offset can never point inside any group -- a
    config mistake, not a runtime data issue, so this must fail loudly at
    call time rather than silently reading the wrong slot from the *next*
    item."""
    with pytest.raises(ValueError, match="offset 3 is out of range for group_size 3"):
        extract_positional_html_items(SPA_HTML, SPA_SLOT_SELECTOR, 3, {"title": "3::text"})
