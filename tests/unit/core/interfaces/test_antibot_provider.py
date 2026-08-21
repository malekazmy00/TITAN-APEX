"""Unit tests for src/core/interfaces/antibot_provider.py."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.core.interfaces.antibot_provider import AntibotProvider, Solution


class _FakeAntibotProvider(AntibotProvider):
    """Minimal concrete implementation used only to exercise the contract."""

    def solve(self, url: str) -> Solution:
        return Solution(
            url=url,
            html="<html></html>",
            status_code=200,
            cookies={"session": "abc"},
            solved_at=datetime.now(tz=UTC),
        )


def test_concrete_implementation_satisfies_the_contract() -> None:
    """Happy path: a full implementation instantiates and returns a Solution."""
    provider: AntibotProvider = _FakeAntibotProvider()
    result = provider.solve("https://example.com")
    assert isinstance(result, Solution)
    assert result.status_code == 200
    assert result.cookies == {"session": "abc"}


def test_abstract_class_cannot_be_instantiated_directly() -> None:
    """Failure case 1: the ABC itself is not instantiable."""
    with pytest.raises(TypeError):
        AntibotProvider()  # type: ignore[abstract]


def test_solution_rejects_invalid_data() -> None:
    """Failure case 2: the Solution model validates its fields."""
    with pytest.raises(ValidationError):
        Solution(
            url="https://example.com",
            html="<html></html>",
            status_code="not-an-int",  # type: ignore[arg-type]
            solved_at=datetime.now(tz=UTC),
        )
