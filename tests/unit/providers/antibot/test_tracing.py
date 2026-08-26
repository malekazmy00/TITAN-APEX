"""Unit tests for src/providers/antibot/_tracing.py.

Both functions are pure (env var read / string+path building) -- no
real browser or filesystem write involved.
"""

from __future__ import annotations

import pytest

from src.providers.antibot._tracing import build_trace_path, trace_dir_from_env


def test_trace_dir_from_env_returns_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path (the default, common case): tracing is off unless
    explicitly turned on."""
    monkeypatch.delenv("TITAN_TRACE_DIR", raising=False)

    assert trace_dir_from_env() is None


def test_trace_dir_from_env_returns_none_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure-adjacent case 1: an empty string is treated the same as
    unset, not as a (meaningless) empty directory path."""
    monkeypatch.setenv("TITAN_TRACE_DIR", "")

    assert trace_dir_from_env() is None


def test_trace_dir_from_env_returns_the_configured_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: a real value (e.g. what ci.yml sets) is read back
    unchanged."""
    monkeypatch.setenv("TITAN_TRACE_DIR", "/tmp/ci-diagnostics/traces")

    assert trace_dir_from_env() == "/tmp/ci-diagnostics/traces"


def test_build_trace_path_lives_under_the_given_directory() -> None:
    path = build_trace_path("/tmp/traces", "https://example.com/feed", "camoufox")

    assert path.startswith("/tmp/traces/")
    assert path.endswith(".zip")


def test_build_trace_path_embeds_the_provider_name() -> None:
    path = build_trace_path("/tmp/traces", "https://example.com/feed", "patchright")

    assert "patchright" in path


def test_build_trace_path_sanitizes_non_alphanumeric_url_characters() -> None:
    """The url is embedded for human skimmability, not parsed back --
    but it must never introduce path separators or other characters
    that could produce something other than a plain filename inside
    trace_dir."""
    path = build_trace_path("/tmp/traces", "https://example.com/feed?x=1&y=2", "camoufox")

    filename = path.rsplit("/", 1)[-1]
    assert "/" not in filename[:-4]  # nothing beyond the one directory separator already asserted
    assert "?" not in filename
    assert "&" not in filename
    assert ":" not in filename


def test_build_trace_path_is_unique_across_two_calls_for_the_same_url() -> None:
    """Failure-adjacent case 2: many crawls against the exact same url
    (e.g. a retried or repeated live test) within one CI job must never
    collide and silently overwrite an earlier trace."""
    first = build_trace_path("/tmp/traces", "https://example.com/feed", "camoufox")
    second = build_trace_path("/tmp/traces", "https://example.com/feed", "camoufox")

    assert first != second


def test_build_trace_path_rejects_an_empty_url() -> None:
    """Failure case 3: nothing meaningful to name a trace after."""
    with pytest.raises(ValueError, match="url must be non-empty"):
        build_trace_path("/tmp/traces", "", "camoufox")
