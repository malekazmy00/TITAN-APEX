#!/usr/bin/env bash
# Checks the kernel ring buffer for real OOM-killer activity -- confirms
# or denies the "memory pressure" hypothesis with direct evidence
# instead of guessing it again (docs/REQUIREMENTS.md section 9's "DOM
# Virtualization Instability" investigation's monitoring-infrastructure
# investment). Always run with `if: always()`, regardless of whether the
# job passed -- a clean run's own "nothing found" is itself useful
# evidence, accumulating across CI runs over time, not just a
# failure-triage tool for one specific incident.
#
# Usage: scripts/ci-check-oom.sh <output-file>
set -uo pipefail
OUT_FILE="${1:?usage: ci-check-oom.sh <output-file>}"

if ! command -v dmesg >/dev/null 2>&1; then
  echo "dmesg not available on this runner -- cannot check for OOM activity." | tee "$OUT_FILE"
  exit 0
fi

# GitHub Actions' ubuntu-latest runners grant passwordless sudo; dmesg
# itself often needs it (kernel.dmesg_restrict defaults to on).
if sudo -n dmesg >"$OUT_FILE" 2>/dev/null; then
  :
elif dmesg >"$OUT_FILE" 2>/dev/null; then
  :
else
  echo "dmesg failed (permission denied even with sudo) -- cannot check for OOM activity." \
    | tee -a "$OUT_FILE"
  exit 0
fi

if grep -qiE 'out of memory|oom-kill|oom_kill|killed process' "$OUT_FILE"; then
  echo "OOM-killer activity FOUND in dmesg for this job -- matching lines:"
  grep -iE 'out of memory|oom-kill|oom_kill|killed process' "$OUT_FILE"
else
  echo "No OOM-killer activity found in dmesg for this job."
fi
