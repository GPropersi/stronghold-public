#!/usr/bin/env bash
#
# push-gate.sh — the deterministic /git-push gate: staleness, path-to-suite scope mapping,
# sequential suite runs, and a HEAD-sha-keyed result file. Run from the sub-repo checkout to push
# (cwd = that checkout, like gh-app-push.sh), at orchestrator level only.
#
# Usage:
#   push-gate.sh --precheck <branch>   staleness + scope only (no suites, writes NO result file)
#   push-gate.sh <branch>              full gate: the same, then the suites; writes
#                                      <git-common-dir>/push-gate/<sha>.json (status running -> pass|fail|na)
#   push-gate.sh --status <branch>     print the stored result path + JSON for the branch tip, or `none`
# Suites come from the `Push gate` seam key of the cwd checkout's CLAUDE.md (never guessed). A
# row's `a && b` command runs as the separate suites `a` and `b`, and a suite named by several
# matched rows runs once (a command with `cd`, `;`, `|`, `||` or other shell state stays whole).
# Read-only check used by the hook: python3 lib/push_gate.py check <branch>
#
# Env: PG_GH (gh binary, default gh-bot.sh next to this script; tests point it at a stub)
#      PG_LOG_DIR (suite logs, default /tmp/claude)
# Exit: 0 pass or na | 1 a suite failed | 2 usage | 3 precondition (stale, dirty tracked files in
#   full mode, HEAD is not refs/heads/<branch>, default branch, unmatched path, nothing to push,
#   Worktree policy denial) | 4 could not run (no Push gate key, malformed row, fetch failed,
#   cannot verify merged-PR state, suite missing)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage_error() {
  echo "push-gate.sh: $1" >&2
  exit 2
}

mode="full"
branch=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help)
      sed -n '2,/^set /{/^set /!p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    --precheck)
      [ "$mode" = "full" ] || usage_error "--precheck and --status are mutually exclusive"
      mode="precheck" ;;
    --status)
      [ "$mode" = "full" ] || usage_error "--precheck and --status are mutually exclusive"
      mode="status" ;;
    -*) usage_error "unknown option '$1'" ;;
    *)
      [ -z "$branch" ] || usage_error "unexpected argument '$1'"
      branch="$1" ;;
  esac
  shift
done

[ -n "$branch" ] || usage_error "usage: push-gate.sh [--precheck | --status] <branch>"

export PG_GH="${PG_GH:-$SCRIPT_DIR/gh-bot.sh}"

if [ "$mode" = "status" ]; then
  exec python3 "$SCRIPT_DIR/lib/push_gate.py" status "$branch"
fi
exec python3 "$SCRIPT_DIR/lib/push_gate.py" run "$mode" "$branch"
