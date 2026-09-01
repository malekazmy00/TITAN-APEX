"""Shared HTML-string field-extraction helper for
``GenericSpider``'s ``extraction_mode: "parsed_html"`` progressive-
collection path (docs/REQUIREMENTS.md section 9 entry 14, the real fix
for entry 13's confirmed DOM Virtualization gap).

Mirrors ``generic_spider.py``'s own (pre-existing) ``_parse_html``
row-extraction logic *exactly* (same 0/1/many -> ``None``/value/list
shape) but operates on a plain HTML string via ``parsel.Selector``
directly, instead of a Scrapy ``Response``'s ``.css()``. Parsel is what
``response.css()`` delegates to internally either way, so this is the
same selector engine, not a different one -- needed as its own function
because entry 14's provider-side ``Solution.html_snapshots`` is a list of
*raw HTML strings*, each one captured by
:func:`~src.providers.antibot._scroll.scroll_and_collect` at one scroll
step (see that module's own docstring for why reading only once, after
scrolling finishes, structurally cannot recover a virtualized list's
evicted content) -- ``GenericSpider`` is the one that parses and merges
them (deduplicated by ``post_id``) once they reach it via
``response.meta``, not the provider itself, since only the caller knows
which field is the real identity key.

Lives under ``src/providers/antibot/`` alongside
:mod:`src.providers.antibot._live_dom` (the ``"live_dom"`` half of the
same fix) rather than in ``src/spiders/`` -- both are field-extraction
strategies paired with entry 12/14's provider-level capabilities, even
though this particular one's only real caller is ``generic_spider.py``
(``src/spiders/pipelines.py`` already imports directly from
``src/providers/storage/`` for an analogous cross-layer reason, so this
isn't a new dependency shape for this codebase).

**Positional/group extraction (docs/REQUIREMENTS.md section 9 entry 23 --
the real, general fix for Known Limitation #5's confirmed
``react-shopping-cart`` gap):** :func:`extract_positional_html_items`
is a second, independent extraction strategy for a target whose items
have *no* stable class or attribute to select by at all -- the real
constraint a CSS-in-JS library (``styled-components``, ``emotion``)
creates: every component gets an opaque, build-time-generated class
name (a fresh hash on every rebuild/deploy), so a YAML config written
today can never hardcode a selector that still matches after the next
build. ``extract_parsed_html_items`` above (and ``_live_dom.py``'s
``extract_live_dom_items``) both assume one item = one element, with
every field reachable as a CSS *descendant* of it -- structurally
unusable here regardless of *which* class-name-based selector is tried,
since there is no class name that will still be valid tomorrow.

The one thing that *does* stay stable across any rebuild: a repeating
list's items render in the same fixed order, the same fixed shape,
every time (this is true of any templated/looped UI, CSS-in-JS or not).
:func:`extract_positional_html_items` exploits exactly that: ``item``
(``SelectorsConfig.item_group_size``'s companion) is reinterpreted as a
*flat* selector matching every field-element of every item, concatenated
together in document order (e.g. every product's image, then title,
then price, if that render order repeats identically per product) --
not one match per item. Every ``item_group_size`` consecutive matches
form one item; ``field_selectors`` values become
``"{offset}::text"``/``"{offset}::attr(name)"`` (``offset`` is the
0-indexed position *within* one group), not a CSS sub-selector -- a
deliberately distinct syntax from ``extract_parsed_html_items``'s own
(CSS-descendant-prefixed) one, unambiguous since this is a wholly
separate code path, only reached when a config explicitly sets
``item_group_size``.
"""

from __future__ import annotations

from typing import Any

from parsel import Selector


def extract_parsed_html_items(
    html: str, item_selector: str, field_selectors: dict[str, str]
) -> list[dict[str, Any]]:
    """Extract every item matching ``item_selector`` from the ``html``
    string, using the exact same parsel/Scrapy CSS-extension mini-language
    (``"::text"``, ``"::attr(name)"``) ``generic_spider.py``'s own
    ``_parse_html`` already uses.

    Raises:
        ValueError: if ``item_selector`` is empty -- there's nothing to
            match.
    """
    if not item_selector:
        raise ValueError("item_selector must be non-empty")

    selector = Selector(text=html)
    items: list[dict[str, Any]] = []
    for row in selector.css(item_selector):
        item: dict[str, Any] = {}
        for field_name, css_expr in field_selectors.items():
            values = row.css(css_expr).getall()
            if len(values) == 0:
                item[field_name] = None
            elif len(values) == 1:
                item[field_name] = values[0]
            else:
                item[field_name] = values
        items.append(item)
    return items


def extract_positional_html_items(
    html: str, slot_selector: str, group_size: int, field_selectors: dict[str, str]
) -> list[dict[str, Any]]:
    """The positional/group extraction strategy this module's own
    docstring describes: ``slot_selector`` matches every field-element of
    every item, flattened together in document order -- every
    ``group_size`` consecutive matches form one item, and
    ``field_selectors`` values (``"{offset}::text"``/``"{offset}::attr(name)"``)
    address a field by its 0-indexed position *within* that group.

    A trailing partial group (``len(matches) % group_size != 0`` -- more
    slots than a whole number of groups, e.g. stray unrelated markup that
    happens to also match ``slot_selector``) is silently dropped, not
    raised: an incomplete group has no well-defined field values for
    *any* of its members, so there is nothing meaningful to return for
    it. The caller (``generic_spider.py``) already logs a clear
    ``WARNING`` when the final item count comes back empty -- the same
    "no items found" signal as every other extraction path here, not a
    silent success with wrong data.

    Unlike :func:`extract_parsed_html_items`'s 0/1/many -> ``None``/value/list
    shape, a field here always resolves to exactly one value (or
    ``None``) -- a "slot" is by construction one single element, so there
    is no multi-match ambiguity to represent as a list.

    Raises:
        ValueError: if ``slot_selector`` is empty, ``group_size`` is not
            positive, a field expression isn't shaped
            ``"{int}::text"``/``"{int}::attr(name)"``, or a field's own
            offset falls outside ``[0, group_size)`` -- a config that
            could never produce a meaningful value for any group.
    """
    if not slot_selector:
        raise ValueError("slot_selector must be non-empty")
    if group_size <= 0:
        raise ValueError(f"group_size must be > 0, got {group_size}")

    parsed_fields = {name: _parse_positional_expr(expr) for name, expr in field_selectors.items()}
    for field_name, (offset, _kind) in parsed_fields.items():
        if not 0 <= offset < group_size:
            raise ValueError(
                f"field {field_name!r} offset {offset} is out of range for "
                f"group_size {group_size} (must be 0 <= offset < {group_size})"
            )

    selector = Selector(text=html)
    slots = selector.css(slot_selector)
    items: list[dict[str, Any]] = []
    for group_start in range(0, len(slots) - group_size + 1, group_size):
        item: dict[str, Any] = {
            field_name: _read_positional_value(slots[group_start + offset], kind)
            for field_name, (offset, kind) in parsed_fields.items()
        }
        items.append(item)
    return items


def _parse_positional_expr(expr: str) -> tuple[int, str]:
    """Parses one positional field expression -- see
    :func:`extract_positional_html_items`'s own docstring for the syntax.

    Raises:
        ValueError: if ``expr`` isn't shaped ``"{int}::text"``/
            ``"{int}::attr(name)"``.
    """
    unsupported = (
        "unsupported positional field expression (expected "
        f"'{{offset}}::text' or '{{offset}}::attr(name)'): {expr!r}"
    )
    offset_part, separator, kind = expr.rpartition("::")
    if not separator:
        raise ValueError(unsupported)
    try:
        offset = int(offset_part)
    except ValueError:
        raise ValueError(unsupported) from None
    if offset < 0:
        raise ValueError(unsupported)
    if kind != "text" and not (kind.startswith("attr(") and kind.endswith(")")):
        raise ValueError(unsupported)
    return offset, kind


def _read_positional_value(slot: Any, kind: str) -> str | None:
    # slot is a parsel Selector; .get() is untyped (Any) in its stubs --
    # same as extract_parsed_html_items's own .getall() above, explicit
    # here only because this function's own return type is annotated.
    value: str | None
    if kind == "text":
        value = slot.css("::text").get()
    else:
        attr_name = kind[len("attr(") : -1]
        value = slot.css(f"::attr({attr_name})").get()
    return value
