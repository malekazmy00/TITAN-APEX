"""Unit tests for src/providers/antibot/_live_dom.py.

Fake Locator/Page objects mimic just enough of Playwright's real
``Locator`` API surface (``.locator()``, ``.count()``, ``.nth()``,
``.text_content()``, ``.get_attribute()``) to exercise this module
without ever launching a real browser.
"""

from __future__ import annotations

from collections.abc import Callable

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


def test_rejects_a_field_expression_with_an_unrecognized_pseudo_after_a_real_separator() -> None:
    """Failure case 5: distinct from the no-separator case above -- a
    real '::' separator is present, but the part after it is neither
    'text' nor 'attr(name)' (e.g. a typo'd pseudo). Same rejection, a
    different code path (the one after target resolution, not the
    upfront no-separator check)."""
    page = _FakePage([_post_row("p1", "alice", "hi")])

    with pytest.raises(ValueError, match="unsupported field expression"):
        extract_live_dom_items(page, '[data-role="post"]', {"author": "span::bogus"})


# --- collect_live_dom_items_progressively (docs/REQUIREMENTS.md section
# 9 entry 14 -- the real fix for entry 13's confirmed DOM Virtualization
# gap) --------------------------------------------------------------


class _FakeVirtualizedPage:
    """A fake Page whose visible rows change between reads -- simulating
    DOM Virtualization's own eviction: the *next* row set in
    ``row_sets_per_read`` is what ``.locator()`` returns each time it's
    called, mimicking a real virtualized list where an earlier window's
    posts are gone by the time a later scroll step is read.

    No scrollHeight scripting -- ``scroll_and_collect`` (docs/REQUIREMENTS.md
    section 9 entry 14's own "Revision") no longer reads or compares it at
    all, since it's not a meaningful signal for a virtualized target."""

    def __init__(self, row_sets_per_read: list[list[_FakeElement]]) -> None:
        self._row_sets = iter(row_sets_per_read)
        self._current_rows: list[_FakeElement] = []
        # docs/REQUIREMENTS.md section 9 entry 17's "Fourth revision":
        # scroll_and_collect drives scrolling via page.mouse.wheel()
        # now, not page.evaluate() -- this fake only needs to accept the
        # call, same "nothing to simulate" shape as the wait_for_timeout
        # stub below.
        self.mouse = _FakeMouse()

    def locator(self, selector: str) -> _FakeLocator:
        assert selector == '[data-role="post"]'
        self._current_rows = next(self._row_sets)
        return _FakeLocator(self._current_rows)

    def evaluate(self, script: str) -> None:
        return None

    def wait_for_timeout(self, ms: int) -> None:
        pass


class _FakeMouse:
    """Stands in for a real Page.mouse -- scroll_and_collect only ever
    calls .move() (once) and .wheel() (per attempt) on it (see
    _FakeVirtualizedPage's own docstring)."""

    def move(self, x: float, y: float) -> None:
        pass

    def wheel(self, delta_x: float, delta_y: float) -> None:
        pass


def test_progressive_collection_keeps_merging_across_every_attempt_not_just_the_first() -> None:
    """Locks in the actual bug entry 14's revision fixed: with the old
    height-growth early exit, collection always stopped after just one
    scroll attempt regardless of max_attempts -- this proves every one
    of several attempts (not only the first) contributes its own
    window's worth of newly-evicted-in posts to the final merge."""
    page = _FakeVirtualizedPage(
        row_sets_per_read=[
            [_post_row("p1", "alice", "hi")],
            [_post_row("p2", "bob", "yo")],
            [_post_row("p3", "carol", "sup")],
            [_post_row("p4", "dave", "hey")],
        ],
    )

    items = collect_live_dom_items_progressively(
        page, '[data-role="post"]', FIELD_SELECTORS, max_attempts=3, pause_ms=700
    )

    assert {item["post_id"] for item in items} == {"p1", "p2", "p3", "p4"}


def test_progressive_collection_merges_across_scroll_steps_deduplicated_by_post_id() -> None:
    """The whole point of entry 14's fix: a post visible only in an
    earlier window (before it's evicted) is still captured, merged with
    whatever's visible in a later window -- not overwritten or lost."""
    page = _FakeVirtualizedPage(
        row_sets_per_read=[
            [_post_row("p1", "alice", "hi")],  # pre-scroll window
            [_post_row("p2", "bob", "yo")],  # post-scroll window (p1 evicted)
        ],
    )

    items = collect_live_dom_items_progressively(
        page, '[data-role="post"]', FIELD_SELECTORS, max_attempts=1, pause_ms=700
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
    )

    items = collect_live_dom_items_progressively(
        page, '[data-role="post"]', FIELD_SELECTORS, max_attempts=1, pause_ms=700
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
    )

    items = collect_live_dom_items_progressively(
        page, '[data-role="post"]', {"text": "span::text"}, max_attempts=1, pause_ms=700
    )

    assert items == []


def test_progressive_collection_passes_trigger_and_wait_fn_through_to_scroll_and_collect() -> None:
    """docs/REQUIREMENTS.md section 9 entry 17's "Fifth revision": this
    module's own trigger_and_wait_fn parameter is a thin passthrough to
    scroll_and_collect's -- confirms it actually reaches it, invoked
    once per scroll attempt, not swallowed along the way."""
    page = _FakeVirtualizedPage(
        row_sets_per_read=[
            [_post_row("p1", "alice", "hi")],
            [_post_row("p2", "bob", "yo")],
            [_post_row("p3", "carol", "sup")],
        ],
    )
    calls = 0

    def trigger_and_wait(trigger: Callable[[], None]) -> bool:
        nonlocal calls
        calls += 1
        trigger()
        return True

    items = collect_live_dom_items_progressively(
        page,
        '[data-role="post"]',
        FIELD_SELECTORS,
        max_attempts=2,
        pause_ms=700,
        trigger_and_wait_fn=trigger_and_wait,
    )

    assert calls == 2  # once per scroll attempt, not the pre-scroll read
    assert {item["post_id"] for item in items} == {"p1", "p2", "p3"}


def test_progressive_collection_defaults_trigger_and_wait_fn_to_none() -> None:
    """Happy path (backward compatibility): every call site written
    before this revision never passes trigger_and_wait_fn -- must keep
    working unchanged."""
    page = _FakeVirtualizedPage(
        row_sets_per_read=[[_post_row("p1", "alice", "hi")], [_post_row("p2", "bob", "yo")]],
    )

    items = collect_live_dom_items_progressively(
        page, '[data-role="post"]', FIELD_SELECTORS, max_attempts=1, pause_ms=700
    )

    assert {item["post_id"] for item in items} == {"p1", "p2"}


def test_progressive_collection_passes_hover_fn_through_to_scroll_and_collect() -> None:
    """docs/REQUIREMENTS.md section 9 entry 17's "Eighth revision"
    (replacing the "Seventh revision"'s container_selector): this
    module's own hover_fn parameter is a thin passthrough to
    scroll_and_collect's -- confirms it actually reaches it, called once
    per scroll attempt (not the fixed page.mouse.move(200, 200) at all)."""
    page = _FakeVirtualizedPage(
        row_sets_per_read=[[_post_row("p1", "alice", "hi")], [_post_row("p2", "bob", "yo")]],
    )
    hover_calls = 0

    def hover_fn() -> bool:
        nonlocal hover_calls
        hover_calls += 1
        return True

    items = collect_live_dom_items_progressively(
        page,
        '[data-role="post"]',
        FIELD_SELECTORS,
        max_attempts=1,
        pause_ms=700,
        hover_fn=hover_fn,
    )

    assert {item["post_id"] for item in items} == {"p1", "p2"}
    assert hover_calls == 1


def test_progressive_collection_uses_a_custom_id_field_when_given() -> None:
    row = _post_row("p1", "alice", "hi")
    page = _FakeVirtualizedPage(
        row_sets_per_read=[[row], [row]],
    )

    items = collect_live_dom_items_progressively(
        page,
        '[data-role="post"]',
        {"author": '[data-role="post-author"]::text'},
        max_attempts=1,
        pause_ms=700,
        id_field="author",
    )

    assert items == [{"author": "alice"}]
