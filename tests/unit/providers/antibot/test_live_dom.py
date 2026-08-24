"""Unit tests for src/providers/antibot/_live_dom.py.

Fake Locator/Page objects mimic just enough of Playwright's real
``Locator`` API surface (``.locator()``, ``.count()``, ``.nth()``,
``.text_content()``, ``.get_attribute()``) to exercise this module
without ever launching a real browser.
"""

from __future__ import annotations

import pytest

from src.providers.antibot._live_dom import (
    collect_live_dom_items_progressively,
    extract_live_dom_items,
)


class _FakeLocator:
    """A fake Playwright Locator scoped to a fixed list of fake elements."""

    def __init__(self, elements: list[_FakeElement]) -> None:
        self._elements = elements

    def count(self) -> int:
        return len(self._elements)

    def nth(self, index: int) -> _FakeLocator:
        return _FakeLocator([self._elements[index]])

    def locator(self, selector: str) -> _FakeLocator:
        # Each fake element resolves its own sub-selectors -- concatenate
        # every element's matches, the same "search within this scope"
        # shape a real Locator.locator() call has.
        matches: list[_FakeElement] = []
        for element in self._elements:
            matches.extend(element.children.get(selector, []))
        return _FakeLocator(matches)

    def text_content(self) -> str:
        assert len(self._elements) == 1
        return self._elements[0].text

    def get_attribute(self, name: str) -> str | None:
        assert len(self._elements) == 1
        return self._elements[0].attrs.get(name)


class _FakeElement:
    def __init__(
        self,
        text: str = "",
        attrs: dict[str, str] | None = None,
        children: dict[str, list[_FakeElement]] | None = None,
    ) -> None:
        self.text = text
        self.attrs = attrs or {}
        self.children = children or {}


class _FakePage:
    def __init__(self, rows: list[_FakeElement]) -> None:
        self._rows = rows

    def locator(self, selector: str) -> _FakeLocator:
        assert selector == '[data-role="post"]'
        return _FakeLocator(self._rows)


def _post_row(post_id: str, author: str, text: str) -> _FakeElement:
    return _FakeElement(
        attrs={"data-post-id": post_id},
        children={
            '[data-role="post-author"]': [_FakeElement(text=author)],
            '[data-role="post-text"]': [_FakeElement(text=text)],
        },
    )


FIELD_SELECTORS = {
    "post_id": "::attr(data-post-id)",
    "author": '[data-role="post-author"]::text',
    "text": '[data-role="post-text"]::text',
}


def test_extracts_one_item_per_matching_row() -> None:
    """Happy path: each row's own attr/text fields resolve correctly,
    including the bare ::attr(...) (of the row itself, no sub-selector)
    and the prefixed ::text (of a descendant) shapes."""
    page = _FakePage([_post_row("p1", "alice", "hi"), _post_row("p2", "bob", "yo")])

    items = extract_live_dom_items(page, '[data-role="post"]', FIELD_SELECTORS)

    assert items == [
        {"post_id": "p1", "author": "alice", "text": "hi"},
        {"post_id": "p2", "author": "bob", "text": "yo"},
    ]


def test_no_matching_rows_yields_an_empty_list() -> None:
    """Happy path (empty case): zero matches is a real, valid result, not
    an error -- generic_spider.py's own no-items-found warning applies at
    the caller level, not here."""
    page = _FakePage([])

    items = extract_live_dom_items(page, '[data-role="post"]', FIELD_SELECTORS)

    assert items == []


def test_missing_field_resolves_to_none() -> None:
    """Failure-adjacent case 1: a field selector matching nothing within a
    row resolves to None -- the same shape response.css(...).getall()
    returning [] already produces in generic_spider.py's string-based path."""
    row = _FakeElement(attrs={"data-post-id": "p1"}, children={})  # no post-author child
    page = _FakePage([row])

    items = extract_live_dom_items(
        page, '[data-role="post"]', {"author": '[data-role="post-author"]::text'}
    )

    assert items == [{"author": None}]


def test_multiple_matches_for_one_field_yield_a_list() -> None:
    """Failure-adjacent case 2: a field selector matching more than one
    descendant returns every match as a list -- mirrors
    response.css(...).getall()'s own multi-match shape."""
    row = _FakeElement(
        children={
            "span.tag": [_FakeElement(text="a"), _FakeElement(text="b")],
        }
    )
    page = _FakePage([row])

    items = extract_live_dom_items(page, '[data-role="post"]', {"tags": "span.tag::text"})

    assert items == [{"tags": ["a", "b"]}]


def test_rejects_an_empty_item_selector() -> None:
    """Failure case 3: nothing meaningful to query with an empty selector."""
    with pytest.raises(ValueError, match="item_selector must be non-empty"):
        extract_live_dom_items(_FakePage([]), "", FIELD_SELECTORS)


def test_text_field_uses_text_content_not_layout_dependent_inner_text() -> None:
    """A real, deliberate choice (see _live_dom.py's own comment): using
    text_content() instead of inner_text() means a hidden
    (display:none, e.g. structural/decoy_data.py's decoy twin) element's
    real text is still extracted, matching parsel's own ::text (which
    never considers visibility either) -- inner_text() would return "" for
    a non-rendered element in a real browser regardless of its actual
    text content. This fake Locator only implements text_content(), not
    inner_text() -- if the implementation ever called the wrong one, this
    (and every other ::text test above) would fail with AttributeError."""
    row = _FakeElement(children={"span": [_FakeElement(text="hidden but real")]})
    page = _FakePage([row])

    items = extract_live_dom_items(page, '[data-role="post"]', {"text": "span::text"})

    assert items == [{"text": "hidden but real"}]


def test_rejects_a_field_expression_without_a_recognized_pseudo() -> None:
    """Failure case 4: a field expression that isn't ::text/::attr(name)
    is a real config bug -- never silently misread as a plain selector."""
    page = _FakePage([_post_row("p1", "alice", "hi")])

    with pytest.raises(ValueError, match="unsupported field expression"):
        extract_live_dom_items(page, '[data-role="post"]', {"author": "plain-selector-no-pseudo"})


# --- collect_live_dom_items_progressively (docs/REQUIREMENTS.md section
# 9 entry 14 -- the real fix for entry 13's confirmed DOM Virtualization
# gap) --------------------------------------------------------------


class _FakeVirtualizedPage:
    """A fake Page whose visible rows change between reads -- simulating
    DOM Virtualization's own eviction: the *next* row set in
    ``row_sets_per_read`` is what ``.locator()`` returns each time it's
    called, mimicking a real virtualized list where an earlier window's
    posts are gone by the time a later scroll step is read."""

    def __init__(self, row_sets_per_read: list[list[_FakeElement]], heights: list[int]) -> None:
        self._row_sets = iter(row_sets_per_read)
        self._current_rows: list[_FakeElement] = []
        self._heights = iter(heights)

    def locator(self, selector: str) -> _FakeLocator:
        assert selector == '[data-role="post"]'
        self._current_rows = next(self._row_sets)
        return _FakeLocator(self._current_rows)

    def evaluate(self, script: str) -> int | None:
        if script == "document.body.scrollHeight":
            return next(self._heights)
        return None

    def wait_for_timeout(self, ms: int) -> None:
        pass


def test_progressive_collection_merges_across_scroll_steps_deduplicated_by_post_id() -> None:
    """The whole point of entry 14's fix: a post visible only in an
    earlier window (before it's evicted) is still captured, merged with
    whatever's visible in a later window -- not overwritten or lost."""
    page = _FakeVirtualizedPage(
        row_sets_per_read=[
            [_post_row("p1", "alice", "hi")],  # pre-scroll window
            [_post_row("p2", "bob", "yo")],  # post-scroll window (p1 evicted)
        ],
        heights=[1000, 1000],  # flat -> stop after the one scroll step
    )

    items = collect_live_dom_items_progressively(
        page, '[data-role="post"]', FIELD_SELECTORS, max_attempts=8, pause_ms=700
    )

    assert items == [
        {"post_id": "p1", "author": "alice", "text": "hi"},
        {"post_id": "p2", "author": "bob", "text": "yo"},
    ]


def test_progressive_collection_does_not_duplicate_a_post_seen_more_than_once() -> None:
    """Failure-adjacent case 1: a post still visible on a later read (not
    yet evicted) must not appear twice."""
    same_post = _post_row("p1", "alice", "hi")
    page = _FakeVirtualizedPage(
        row_sets_per_read=[[same_post], [same_post]],
        heights=[1000, 1000],
    )

    items = collect_live_dom_items_progressively(
        page, '[data-role="post"]', FIELD_SELECTORS, max_attempts=8, pause_ms=700
    )

    assert items == [{"post_id": "p1", "author": "alice", "text": "hi"}]


def test_progressive_collection_ignores_items_with_no_id_field_value() -> None:
    """Failure-adjacent case 2: an item whose id_field resolves to None
    (missing from field_selectors, or absent on that element) is dropped
    from the merge -- there's no way to deduplicate what has no identity,
    and silently keying on None would wrongly merge unrelated items."""
    no_id_row = _FakeElement(children={"span": [_FakeElement(text="x")]})
    page = _FakeVirtualizedPage(
        row_sets_per_read=[[no_id_row], [no_id_row]],
        heights=[1000, 1000],
    )

    items = collect_live_dom_items_progressively(
        page, '[data-role="post"]', {"text": "span::text"}, max_attempts=8, pause_ms=700
    )

    assert items == []


def test_progressive_collection_uses_a_custom_id_field_when_given() -> None:
    row = _post_row("p1", "alice", "hi")
    page = _FakeVirtualizedPage(
        row_sets_per_read=[[row], [row]],
        heights=[1000, 1000],
    )

    items = collect_live_dom_items_progressively(
        page,
        '[data-role="post"]',
        {"author": '[data-role="post-author"]::text'},
        max_attempts=8,
        pause_ms=700,
        id_field="author",
    )

    assert items == [{"author": "alice"}]
