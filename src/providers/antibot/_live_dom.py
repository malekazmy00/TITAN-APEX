"""Shared live-DOM field-extraction helper for
:class:`~src.providers.antibot.camoufox_provider.CamoufoxProvider` and
:class:`~src.providers.antibot.patchright_provider.PatchrightProvider`'s
browser-driving solve functions.

Reads directly from Playwright's own live ``Locator`` API instead of a
serialized HTML string -- Playwright's locator CSS engine auto-pierces
*open* shadow roots by default (its own documented behavior; only a
``:light()``-scoped selector opts back out of that), unlike any
string-based HTML parser (Scrapy/parsel included), which never even sees
a shadow root's content in the first place -- it was never part of the
serialized ``outerHTML``/``innerHTML`` to begin with, per the DOM spec
(docs/REQUIREMENTS.md section 9 entry 11's real, confirmed Shadow DOM
gap; entry 12 is this module's own fix for it).

Field selector syntax matches ``SelectorsConfig.fields`` values *exactly*
-- the same parsel/Scrapy CSS-extension mini-language (``"::text"``,
``"::attr(name)"``, optionally prefixed with a plain CSS sub-selector,
e.g. ``'[data-role="post-author"]::text'``) already used for the
string-based extraction path (``generic_spider.py``'s ``_parse_html``),
reused unchanged here so one YAML config works identically regardless of
``extraction_mode`` -- no second selector language to maintain.

Typed loosely (``Any`` for the live Playwright/Patchright objects
themselves) on purpose: both providers hand this genuinely equivalent,
duck-typed live objects, but Patchright is a drop-in Playwright
*replacement*, not the same importable type
(``patchright.sync_api.Locator`` and ``playwright.sync_api.Locator`` are
structurally identical but distinct classes) -- the same ``Any``
tradeoff ``camoufox_provider.py``'s own module docstring already
documents for these real, dynamically-duck-typed objects.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def extract_live_dom_items(
    page: Any, item_selector: str, field_selectors: dict[str, str]
) -> list[dict[str, Any]]:
    """Extract every item matching ``item_selector`` directly from
    ``page``'s live DOM, piercing open shadow roots automatically.

    Raises:
        ValueError: if ``item_selector`` is empty -- there's nothing to
            match -- or if any ``field_selectors`` value uses a pseudo
            other than ``::text``/``::attr(name)`` (see
            :func:`_extract_field`).
    """
    if not item_selector:
        raise ValueError("item_selector must be non-empty")

    rows = page.locator(item_selector)
    items: list[dict[str, Any]] = []
    for i in range(rows.count()):
        row = rows.nth(i)
        items.append({name: _extract_field(row, expr) for name, expr in field_selectors.items()})
    return items


def _extract_field(row: Any, expr: str) -> Any:
    """Resolves one parsel-style field expression against ``row``.

    ``"::text"``/``"::attr(name)"`` alone means "of ``row`` itself";
    prefixed with a plain CSS selector (e.g. ``'[data-role="x"]::text'``)
    means "of a descendant matching that selector" -- mirroring
    ``response.css(css_expr).getall()``'s own 0/1/many-match semantics
    (``None``/a single value/a list) that ``generic_spider.py``'s
    string-based path already has, so a caller sees an identical shape
    regardless of ``extraction_mode``.

    Raises:
        ValueError: if ``expr`` doesn't use ``::text`` or ``::attr(name)``
            -- not a shape any of this project's own configs produce
            (every real ``.yaml`` here uses one or the other exclusively),
            but never silently misread as a plain selector either.
    """
    unsupported = f"unsupported field expression (expected ::text or ::attr(name)): {expr!r}"
    selector_part, separator, kind = expr.rpartition("::")
    if not separator:
        raise ValueError(unsupported)
    target = row.locator(selector_part) if selector_part else row

    if kind == "text":
        # `text_content()` (raw ``element.textContent``), not
        # `inner_text()` (rendered, layout-dependent ``element.innerText``
        # -- real browsers return "" for a non-rendered, e.g.
        # `display:none`, element regardless of its actual text).
        # `structural/decoy_data.py`'s hidden decoy twin is exactly that
        # shape, and parsel's own ``::text`` (the string-based path this
        # mirrors) never considers visibility at all -- matching that
        # here means one config's field VALUES stay identical between
        # extraction_mode "parsed_html" and "live_dom" for the same DOM
        # content; only *reachability* into a shadow root differs.
        return _all_or_none(target, lambda t: t.text_content())
    if kind.startswith("attr(") and kind.endswith(")"):
        attr_name = kind[len("attr(") : -1]
        return _all_or_none(target, lambda t: t.get_attribute(attr_name))
    raise ValueError(unsupported)


def _all_or_none(target: Any, get_value: Callable[[Any], str | None]) -> Any:
    count = target.count()
    if count == 0:
        return None
    if count == 1:
        return get_value(target)
    return [get_value(target.nth(i)) for i in range(count)]
