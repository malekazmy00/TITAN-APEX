"""Unit tests for src/core/interfaces/antibot_provider.py."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.core.interfaces.antibot_provider import (
    AntibotProvider,
    LiveDomSelectors,
    LoginFlow,
    Solution,
)


class _FakeAntibotProvider(AntibotProvider):
    """Minimal concrete implementation used only to exercise the contract."""

    def solve(
        self,
        url: str,
        click_selector: str | None = None,
        extraction_selectors: LiveDomSelectors | None = None,
        progressive_extraction: bool = False,
        login_flow: LoginFlow | None = None,
        warm_session_urls: list[str] | None = None,
        use_accumulated_profile: bool = False,
        user_agent_override: str | None = None,
    ) -> Solution:
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


def test_solution_items_defaults_to_none() -> None:
    """docs/REQUIREMENTS.md section 9 entry 12: every existing call site
    (every provider before this round) must keep constructing a Solution
    with no `items` at all -- it must default to None, not an empty list,
    since the two mean different things (see Solution.items' own comment)."""
    solution = Solution(
        url="https://example.com",
        html="<html></html>",
        status_code=200,
        solved_at=datetime.now(tz=UTC),
    )

    assert solution.items is None


def test_solution_accepts_a_populated_items_list() -> None:
    solution = Solution(
        url="https://example.com",
        html="<html></html>",
        status_code=200,
        items=[{"author": "alice"}],
        solved_at=datetime.now(tz=UTC),
    )

    assert solution.items == [{"author": "alice"}]


def test_live_dom_selectors_rejects_empty_fields() -> None:
    """Failure case 3: an item selector with no fields to extract is a
    real misconfiguration, same reasoning SelectorsConfig's own
    `fields: dict[str, str] = Field(min_length=1)` already enforces."""
    with pytest.raises(ValidationError):
        LiveDomSelectors(item='[data-role="post"]', fields={})
