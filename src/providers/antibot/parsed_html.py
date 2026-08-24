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
