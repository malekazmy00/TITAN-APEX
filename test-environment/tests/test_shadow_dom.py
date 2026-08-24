"""Unit tests for mock-target/structural/shadow_dom.py."""

from __future__ import annotations

import base64
import json

import pytest
from content_generator import generate_comment, generate_post
from structural.shadow_dom import (
    SHADOW_ATTACH_SCRIPT,
    encode_shadow_payload,
    is_shadow_wrapped,
)


def test_even_indices_are_not_shadow_wrapped() -> None:
    """Happy path: light-DOM posts, unchanged from before this layer existed."""
    assert is_shadow_wrapped(0) is False
    assert is_shadow_wrapped(2) is False
    assert is_shadow_wrapped(8) is False


def test_odd_indices_are_shadow_wrapped() -> None:
    """Happy path: every other post moves into a real shadow root."""
    assert is_shadow_wrapped(1) is True
    assert is_shadow_wrapped(3) is True
    assert is_shadow_wrapped(9) is True


def test_is_shadow_wrapped_rejects_a_negative_index() -> None:
    """Failure case 1: there's no such post to classify."""
    with pytest.raises(ValueError, match="index must be >= 0"):
        is_shadow_wrapped(-1)


def test_encode_shadow_payload_round_trips_post_content() -> None:
    """Happy path: decoding the base64(JSON) payload recovers exactly what
    the client-side SHADOW_ATTACH_SCRIPT needs to rebuild the post."""
    post = generate_post("session-a", 1)
    post.comments = [generate_comment("session-a", 1, 0)]

    encoded = encode_shadow_payload(post)
    decoded = json.loads(base64.b64decode(encoded))

    assert decoded["id"] == post.post_id
    assert decoded["author"] == post.author
    assert decoded["text"] == post.text
    assert decoded["likes"] == post.likes
    assert len(decoded["comments"]) == 1
    assert decoded["comments"][0]["id"] == post.comments[0].comment_id


def test_encode_shadow_payload_includes_nested_reply_comments() -> None:
    """Failure-adjacent case 1: a comment's replies must survive encoding
    too, not just its top-level fields."""
    post = generate_post("session-a", 2)
    reply = generate_comment("session-a", 2, 0, depth=1)
    top = generate_comment("session-a", 2, 0)
    top.replies = [reply]
    post.comments = [top]

    decoded = json.loads(base64.b64decode(encode_shadow_payload(post)))

    assert len(decoded["comments"][0]["replies"]) == 1
    assert decoded["comments"][0]["replies"][0]["id"] == reply.comment_id


def test_encode_shadow_payload_rejects_an_empty_post_id() -> None:
    """Failure case 2: nothing meaningful to encode without an id."""
    post = generate_post("session-a", 0)
    post.post_id = ""

    with pytest.raises(ValueError, match="post_id must be non-empty"):
        encode_shadow_payload(post)


def test_encode_shadow_payload_is_deterministic_for_the_same_post() -> None:
    post = generate_post("session-a", 0)

    assert encode_shadow_payload(post) == encode_shadow_payload(post)


def test_attach_script_references_attach_shadow_and_the_payload_attribute() -> None:
    """Sanity check on the shipped script itself: it must actually call
    the real `attachShadow` API against the `mock-shadow-post` placeholder
    and its `data-shadow-payload` attribute -- a typo here would silently
    make the whole layer inert in a real browser (no test in this
    process-only suite can execute real client-side JS to catch that
    otherwise)."""
    assert "attachShadow" in SHADOW_ATTACH_SCRIPT
    assert "mock-shadow-post" in SHADOW_ATTACH_SCRIPT
    assert "shadowPayload" in SHADOW_ATTACH_SCRIPT
