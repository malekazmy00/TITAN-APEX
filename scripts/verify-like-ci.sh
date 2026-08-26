#!/usr/bin/env bash
# Runs the exact commands .github/workflows/{lint,ci}.yml gate on, in the
# same order, against the current working tree -- so a local "looks
# green" claim can never again quietly diverge from what CI actually
# checks.
#
# Why this exists (docs/REQUIREMENTS.md section 9 entry 17's own,
# explicitly-recorded mistake, not a hypothetical): local validation
# once ran `pytest tests/unit tests/contract --cov=src --cov-fail-under=85`
# together, which reports a higher, misleading coverage number than
# CI's real gate -- ci.yml's "Unit tests (coverage gate >= 85%)" step
# runs `pytest tests/unit` ALONE. The gap (85.13% reported locally vs.
# 84.89% real) was just narrow enough to pass locally and fail on CI
# (run 32977436823) -- confirmed by reproducing the exact CI command
# locally afterward. This script is that reproduction, kept around
# instead of re-derived by hand every time, specifically so this
# mistake has no third occurrence.
#
# What this script CAN verify in a sandbox with no Docker daemon and no
# real browser-launch capacity confirmed (this project's own documented
# constraint -- see docs/REQUIREMENTS.md's repeated "no Docker daemon
# available" notes): lint.yml in full, and ci.yml's unit / contract /
# test-environment-unit steps. What it deliberately does NOT attempt
# (and says so loudly, rather than silently skipping): docker compose
# stack + tests/integration -- those need real GitHub Actions CI, same
# as this whole project's established "verify don't assume environment
# constraints" discipline requires.
#
# Two deliberate deviations from {lint,ci}.yml's literal commands, not a
# mismatch with CI's intent -- both are this *sandbox's* PATH resolving
# a bare binary to a different, unrelated install than the project's
# real environment, confirmed by hand, not assumed:
#   - lint.yml runs bare `mypy src/ --strict`, but here that resolves to
#     an environment missing pydantic/camoufox/patchright/playwright
#     stubs this project's own venv has (docs/REQUIREMENTS.md section 9
#     entry 14 already documented this). `python -m mypy` is correct.
#   - ci.yml runs bare `pytest ...`, but here that resolves to pytest
#     9.0.2 with no pytest-cov plugin registered at all (`--cov` itself
#     is rejected as an unrecognized argument) -- a completely different
#     install from `python -m pytest`'s 8.4.2 + pytest-cov/Faker this
#     project actually uses. `python -m pytest` is correct.
# A real CI runner does not have either quirk; on a machine where the
# bare binaries already resolve correctly, `python -m ...` still behaves
# identically -- this is strictly the safer invocation, never a
# different one.

set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== lint.yml: ruff check src/ tests/ ==="
ruff check src/ tests/

echo
echo "=== lint.yml: mypy src/ --strict (via 'python -m mypy' -- see this script's own header comment) ==="
python -m mypy src/ --strict

echo
echo "=== ci.yml: Unit tests (coverage gate >= 85%) ==="
python -m pytest tests/unit -v --cov=src --cov-fail-under=85

echo
echo "=== ci.yml: Contract tests ==="
python -m pytest tests/contract -v

echo
echo "=== ci.yml: test-environment unit tests (coverage gate >= 85%) ==="
(cd test-environment && python -m pytest tests/ -v --cov=mock-target --cov-fail-under=85)

echo
echo "All locally-runnable CI/lint steps passed."
echo "NOT verified here -- needs real GitHub Actions CI: docker compose stack + tests/integration."
