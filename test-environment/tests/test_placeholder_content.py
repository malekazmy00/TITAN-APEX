"""Unit tests for mock-target/structural/placeholder_content.py."""

from __future__ import annotations

import pytest
from structural.placeholder_content import render_swap_script


def test_render_swap_script_contains_the_given_delay() -> None:
    """Happy path: the configured delay actually appears in the emitted JS,
    not a hardcoded default."""
    script = render_swap_script(750)

    assert "}, 750);" in script
    assert "data-real-text" in script


def test_render_swap_script_rejects_zero_delay() -> None:
    """Failure case 1: a zero delay would swap immediately, defeating the
    point of testing timing-dependent leakage."""
    with pytest.raises(ValueError, match="delay_ms must be > 0"):
        render_swap_script(0)


def test_render_swap_script_rejects_negative_delay() -> None:
    """Failure case 2: a negative delay is meaningless."""
    with pytest.raises(ValueError, match="delay_ms must be > 0"):
        render_swap_script(-100)
