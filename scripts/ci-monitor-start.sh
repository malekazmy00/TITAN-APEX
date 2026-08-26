#!/usr/bin/env bash
# Starts two background polling loops that run for this CI job's entire
# duration -- docs/REQUIREMENTS.md section 9's "DOM Virtualization
# Instability" investigation's monitoring-infrastructure investment
# (explicit user request after two CI attempts of a code-only fix left
# the real root cause unconfirmed -- see that entry for the full
# context). This gives any future timing/resource investigation on this
# project -- not scoped to that one entry -- direct evidence to open
# instead of re-guessing it.
#
#   docker-stats.log  -- per-container CPU/Memory/Network (docker stats).
#                         Empty/harmless before any container exists yet
#                         -- this is started early (before
#                         test-environment's own stack comes up), on
#                         purpose, to cover the whole job.
#   runner-stats.log   -- the GitHub Actions runner VM itself (not just
#                         our containers): memory, load average, disk.
#                         A container-only view can miss real pressure
#                         from something else entirely on the same host.
#
# Not itself diagnostic -- this only starts the loops and records their
# PIDs so scripts/ci-monitor-stop.sh can cleanly stop them later,
# regardless of whether the job that ran in between passed or failed.
#
# Usage: scripts/ci-monitor-start.sh <output-dir>
set -euo pipefail
OUT_DIR="${1:?usage: ci-monitor-start.sh <output-dir>}"
mkdir -p "$OUT_DIR"

(
  while true; do
    {
      echo "=== $(date -u +%Y-%m-%dT%H:%M:%S.%3NZ) ==="
      docker stats --no-stream --format \
        'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}' 2>/dev/null \
        || echo "(no containers yet, or docker stats failed)"
    } >>"$OUT_DIR/docker-stats.log"
    sleep 1
  done
) &
echo $! >"$OUT_DIR/docker-stats.pid"

(
  while true; do
    {
      echo "=== $(date -u +%Y-%m-%dT%H:%M:%S.%3NZ) ==="
      free -m
      echo "--- loadavg: $(cat /proc/loadavg) ---"
      df -h / 2>/dev/null
    } >>"$OUT_DIR/runner-stats.log"
    sleep 1
  done
) &
echo $! >"$OUT_DIR/runner-stats.pid"

echo "Resource monitors started -- docker-stats.pid=$(cat "$OUT_DIR/docker-stats.pid")" \
  "runner-stats.pid=$(cat "$OUT_DIR/runner-stats.pid")"
