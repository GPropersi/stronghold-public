#!/usr/bin/env bash
#
# pr-state.sh — merged state AND CI state of a PR in one call (the PR itself is the source of truth).
#
# Usage:
#   pr-state.sh <repo-dir> <pr-number> [--wait] [--timeout S] [--interval S]
#
# Prints state, mergeState, auto-merge, the latest attempt of every check (reruns collapse), and a final
# `VERDICT:` line: MERGED | CLOSED | OPEN-CI-FAILED | OPEN-CI-RUNNING | OPEN-BLOCKED | OPEN-READY. A merged PR
# whose checks failed late prints MERGED plus `LATE-FAILURE:` lines to investigate. --wait re-reads (default every
# 30s, up to 1700s) until MERGED, CLOSED or a failed check, printing only when the verdict changes; run it with
# run_in_background and read the VERDICT when it exits. Full semantics: lib/pr_state.py --help.
#
# Env: STRONGHOLD_DIR  override the stronghold root (test seam)
#      PR_STATE_GH     gh wrapper to run (default gh-bot.sh)
# Exit: 0 merged | 1 closed unmerged | 3 open + CI failed | 5 still open (or --wait timeout) | 2 usage | 4 unavailable
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "${1:-}" in
  -h|--help)
    sed -n '2,/^set /{/^set /!p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0 ;;
esac

exec python3 "$SCRIPT_DIR/lib/pr_state.py" "$@"
