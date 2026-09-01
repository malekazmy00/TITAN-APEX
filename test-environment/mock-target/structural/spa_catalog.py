"""SPA catalog: a real hydration-delayed, CSS-in-JS-shaped product grid.

docs/REQUIREMENTS.md section 9 entry 23 (Phase 2 بند 6, Known Limitation
#5's real fix -- the ``react-shopping-cart`` gap: ``styled-components``
generates a fresh, opaque class name for every component every build,
and every product's fields are *siblings* under a container whose own
class is equally unselectable, so no CSS-descendant-based extraction
strategy can ever hardcode a working selector).

``templates/spa_catalog.html`` renders a skeleton with zero real product
markup at all -- the actual grid (each product's image/title/price as
three flat, un-wrapped, opaque-classed sibling elements, in a fixed,
repeating order) is built entirely by inline client-side JS after a
configurable delay, from a small JSON payload embedded in the page --
the same "server renders a shell, JS fills it in from its own embedded
state" shape a real SPA's hydration has, with a minimal (no Redux) cart
state object that updates live on an "Add to Cart" click, purely to
demonstrate the page's content genuinely depends on in-page JS state,
not just static server HTML.

This module only holds the plain-data conversion the template needs
(``Product`` dataclasses aren't directly JSON-serializable via Jinja's
``tojson``) -- the actual hashed/opaque class names come from the
already-existing ``structural.markup_randomizer.MarkupRandomizer``
(``app.py``'s own shared instance, extended with this challenge's own
logical names) -- reused, not duplicated, since its whole job is
already "generate an opaque token per logical element name, rotate it
periodically" -- exactly what CSS-in-JS's build-time hashing needs to
look like here.
"""

from __future__ import annotations

from typing import Any

from content_generator import Product

HYDRATION_SKELETON_TEXT = "Loading products…"


def products_to_payload(products: list[Product]) -> list[dict[str, Any]]:
    """Plain-dict view of ``products``, JSON-serializable via Jinja's
    ``tojson`` filter -- the client-side hydration script's own source
    of truth for what to render (this module's own docstring)."""
    return [
        {
            "product_id": product.product_id,
            "title": product.title,
            "price": product.price,
            "image_url": product.image_url,
        }
        for product in products
    ]
