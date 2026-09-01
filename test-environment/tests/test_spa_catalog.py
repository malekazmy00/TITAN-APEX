"""Unit tests for mock-target/structural/spa_catalog.py."""

from __future__ import annotations

from content_generator import Product
from structural.spa_catalog import HYDRATION_SKELETON_TEXT, products_to_payload


def test_products_to_payload_converts_every_field() -> None:
    """Happy path: every Product field lands in the payload dict."""
    products = [Product(product_id="p1", title="Widget", price=9.99, image_url="/img/1.png")]

    payload = products_to_payload(products)

    assert payload == [
        {"product_id": "p1", "title": "Widget", "price": 9.99, "image_url": "/img/1.png"}
    ]


def test_products_to_payload_preserves_order() -> None:
    """Failure-adjacent case 1: item order must survive the conversion --
    the client-side script renders in this exact order, and the
    positional extraction strategy that reads it back depends on a fixed,
    predictable order."""
    products = [
        Product(product_id="p1", title="A", price=1.0, image_url="/a.png"),
        Product(product_id="p2", title="B", price=2.0, image_url="/b.png"),
    ]

    payload = products_to_payload(products)

    assert [item["product_id"] for item in payload] == ["p1", "p2"]


def test_products_to_payload_of_an_empty_list_is_an_empty_list() -> None:
    """Failure-adjacent case 2: no products is a real, valid (empty) result."""
    assert products_to_payload([]) == []


def test_hydration_skeleton_text_is_non_empty() -> None:
    """Failure case 3: an empty skeleton string would render as truly
    blank content, not a real loading indicator."""
    assert HYDRATION_SKELETON_TEXT.strip()
