"""Unit tests for src/providers/antibot/_tracing.py.

Both functions are pure (env var read / string+path building) -- no
real browser or filesystem write involved.
"""

from __future__ import annotations

import json

import pytest

from src.providers.antibot._tracing import (
    apparmor_denial_delta,
    build_load_event_log_path,
    build_trace_path,
    count_apparmor_camoufox_denials,
    load_event_log_dir_from_env,
    render_load_event_log,
    trace_dir_from_env,
)

_DENIED_LINE = (
    '[  566.812451] audit: type=1400 audit(1787786960.799:143): '
    'apparmor="DENIED" operation="capable" class="cap" '
    'profile="unprivileged_userns" pid=10915 comm="camoufox-bin" '
    'capability=21  capname="sys_admin"'
)
_UNRELATED_LINE = (
    '[  566.810200] audit: type=1400 audit(1787786960.797:142): '
    'apparmor="AUDIT" operation="userns_create" class="namespace" '
    'info="Userns create - transitioning profile" profile="unconfined" '
    'pid=10913 comm="camoufox-bin" requested="userns_create" '
    'target="unprivileged_userns"'
)


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


# --- load_event_log_dir_from_env / build_load_event_log_path /
# render_load_event_log (docs/REQUIREMENTS.md section 9 entry 17's
# "expand the diagnostic tool" phase -- the performance.now()/
# page.expose_function() load-event timeline) --------------------------


def test_load_event_log_dir_from_env_returns_none_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path (the default, common case): no file dump unless
    explicitly turned on."""
    monkeypatch.delenv("TITAN_LOAD_EVENT_LOG_DIR", raising=False)

    assert load_event_log_dir_from_env() is None


def test_load_event_log_dir_from_env_returns_none_when_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure-adjacent case 1: an empty string is treated the same as
    unset, not as a (meaningless) empty directory path."""
    monkeypatch.setenv("TITAN_LOAD_EVENT_LOG_DIR", "")

    assert load_event_log_dir_from_env() is None


def test_load_event_log_dir_from_env_returns_the_configured_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: a real value is read back unchanged."""
    monkeypatch.setenv("TITAN_LOAD_EVENT_LOG_DIR", "/tmp/ci-diagnostics/load-events")

    assert load_event_log_dir_from_env() == "/tmp/ci-diagnostics/load-events"


def test_build_load_event_log_path_lives_under_the_given_directory() -> None:
    path = build_load_event_log_path("/tmp/load-events", "https://example.com/feed", "camoufox")

    assert path.startswith("/tmp/load-events/")
    assert path.endswith(".jsonl")


def test_build_load_event_log_path_is_unique_across_two_calls_for_the_same_url() -> None:
    """Failure-adjacent case 2: many crawls against the exact same url
    within one CI job must never collide and silently overwrite an
    earlier timeline."""
    first = build_load_event_log_path("/tmp/load-events", "https://example.com/feed", "camoufox")
    second = build_load_event_log_path("/tmp/load-events", "https://example.com/feed", "camoufox")

    assert first != second


def test_build_load_event_log_path_rejects_an_empty_url() -> None:
    """Failure case 3: nothing meaningful to name a timeline after."""
    with pytest.raises(ValueError, match="url must be non-empty"):
        build_load_event_log_path("/tmp/load-events", "", "camoufox")


def test_render_load_event_log_writes_one_json_line_per_event() -> None:
    """Happy path: each event dict becomes its own JSON line, in order."""
    events = [
        {"event": "enter", "js_performance_now_ms": 1.0},
        {"event": "fetch_start", "js_performance_now_ms": 2.5},
    ]

    rendered = render_load_event_log(events)

    lines = rendered.splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == events[0]
    assert json.loads(lines[1]) == events[1]


def test_render_load_event_log_returns_empty_string_for_no_events() -> None:
    """Failure-adjacent case: a solve that never triggered a single
    load-event (e.g. progressive_extraction wasn't used at all) renders
    as an empty string, not a stray blank line a naive ``"\\n".join``
    would produce."""
    assert render_load_event_log([]) == ""


# --- count_apparmor_camoufox_denials (docs/REQUIREMENTS.md section 9's
# "DOM Virtualization Instability" investigation, a follow-up user
# request after scripts/ci-check-oom.sh found real, job-wide AppArmor
# denials for camoufox-bin) ---------------------------------------------


def test_count_apparmor_camoufox_denials_counts_a_real_denied_line() -> None:
    """Happy path: a real DENIED line (verbatim shape from an actual CI
    run's dmesg, run 33007271916 attempt 2) is counted."""
    assert count_apparmor_camoufox_denials(_DENIED_LINE) == 1


def test_count_apparmor_camoufox_denials_ignores_non_denied_camoufox_lines() -> None:
    """Failure-adjacent case 1: an AppArmor line that mentions
    camoufox-bin but isn't itself a DENIED entry (e.g. the AUDIT
    "transitioning profile" line that always precedes a DENIED one in
    a real dmesg) must not be miscounted as a denial."""
    assert count_apparmor_camoufox_denials(_UNRELATED_LINE) == 0


def test_count_apparmor_camoufox_denials_ignores_denied_lines_for_other_processes() -> None:
    """Failure-adjacent case 2: a DENIED line for some other process
    must not be attributed to camoufox."""
    other_process_denied = _DENIED_LINE.replace("camoufox-bin", "some-other-proc")

    assert count_apparmor_camoufox_denials(other_process_denied) == 0


def test_count_apparmor_camoufox_denials_sums_across_multiple_lines() -> None:
    """Happy path: a real dmesg blob has many lines -- every matching one
    counts, not just the first."""
    text = "\n".join([_UNRELATED_LINE, _DENIED_LINE, _UNRELATED_LINE, _DENIED_LINE, _DENIED_LINE])

    assert count_apparmor_camoufox_denials(text) == 3


def test_count_apparmor_camoufox_denials_returns_zero_for_empty_text() -> None:
    """Failure case 3: an empty dmesg blob (e.g. dmesg itself failed and
    the caller passed through whatever it got) is zero denials, not an
    error -- this function never needs to fail, only count."""
    assert count_apparmor_camoufox_denials("") == 0


# --- apparmor_denial_delta -----------------------------------------------


def test_apparmor_denial_delta_computes_the_real_difference() -> None:
    """Happy path: more denials happened during the solve than before it
    started."""
    assert apparmor_denial_delta(3, 7) == 4


def test_apparmor_denial_delta_is_zero_when_nothing_new_happened() -> None:
    """Happy path: a genuinely clean solve, zero *new* denials -- a real
    0, not None."""
    assert apparmor_denial_delta(5, 5) == 0


def test_apparmor_denial_delta_is_none_when_the_before_snapshot_failed() -> None:
    """Failure-adjacent case 1: dmesg couldn't be read before the solve
    started -- the delta is meaningless, must be None, not a
    misleadingly-real-looking number."""
    assert apparmor_denial_delta(None, 5) is None


def test_apparmor_denial_delta_is_none_when_the_after_snapshot_failed() -> None:
    """Failure-adjacent case 2: same, for the after snapshot."""
    assert apparmor_denial_delta(5, None) is None


def test_apparmor_denial_delta_is_none_when_both_snapshots_failed() -> None:
    """Failure case 3: neither reading worked at all."""
    assert apparmor_denial_delta(None, None) is None
