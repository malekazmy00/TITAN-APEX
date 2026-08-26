#!/usr/bin/env bash
# Stops the background loops scripts/ci-monitor-start.sh started.
# Always run with `if: always()` in CI -- a mid-job failure must not
# leave orphaned polling loops running past job teardown (harmless on a
# throwaway CI runner, but no reason to leave them, and a leftover
# `docker stats` loop could interfere with the "test-environment stack
# teardown" step that runs right after this one).
#
# Usage: scripts/ci-monitor-stop.sh <output-dir>
set -uo pipefail
OUT_DIR="${1:?usage: ci-monitor-stop.sh <output-dir>}"

for name in docker-stats runner-stats; do
  pid_file="$OUT_DIR/$name.pid"
  if [ -f "$pid_file" ]; then
    pid="$(cat "$pid_file")"
    # The loop's own children (the sleep/docker/free processes it
    # spawned each iteration) too -- killing just the subshell's own PID
    # can leave one last already-forked child running to completion.
    pkill -P "$pid" 2>/dev/null || true
    kill "$pid" 2>/dev/null || true
    echo "Stopped $name (pid $pid)"
  else
    echo "$name.pid not found -- nothing to stop (monitor never started, or already stopped)"
  fi
done
