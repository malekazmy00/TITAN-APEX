"""Unit tests for mock-target/structural/honeypots.py."""

from __future__ import annotations

import pytest
from structural.honeypots import HIDE_METHODS, generate_honeypot_links


def test_generates_requested_count_cycling_through_hide_methods() -> None:
    """Happy path: count links, each with a unique token and a real hide method."""
    tokens = iter(["t0", "t1", "t2", "t3", "t4"])
    links = generate_honeypot_links(count=5, token_factory=lambda: next(tokens))

    assert len(links) == 5
    assert {link.token for link in links} == {"t0", "t1", "t2", "t3", "t4"}
    assert all(link.hide_method in HIDE_METHODS for link in links)
    assert all(link.url == f"/honeypot-trap/{link.token}" for link in links)


def test_rejects_non_positive_count() -> None:
    """Failure case 1: zero/negative honeypots is a misconfiguration."""
    with pytest.raises(ValueError, match="count must be > 0"):
        generate_honeypot_links(count=0)


def test_default_token_factory_produces_unique_tokens() -> None:
    """Failure-adjacent case 2: without an injected factory, real random tokens
    must still be unique (a fixed/predictable default would defeat the point
    of a honeypot -- a scraper could learn to skip a known token)."""
    links = generate_honeypot_links(count=4)

    assert len({link.token for link in links}) == 4
