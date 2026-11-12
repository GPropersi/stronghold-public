#!/usr/bin/env bash
#
# wait-capacity.sh — block, in ONE call, until a repo has room for another spoke/stack.
#
# `make wt-new` is refused ("spoke admission: refusing spoke") while the host lacks usable memory.
# An agent must not hand that wait back to the user or burn a model turn per poll: run this once,
# then retry the refused command when it exits 0.
#
# Usage:
#   wait-capacity.sh <repo-dir> [--timeout <S>] [--interval <S>]
#
#   <repo-dir>      repo whose `stack_ceiling` (last column of `wt-capacity.sh <repo>`) must be > 0
#   --timeout <S>   give up after S seconds (default 1500; clamped to WAIT_CAPACITY_MAX, default 1700)
#   --interval <S>  seconds between checks (default 30)
#
# A repo with no cost model (`n/a` ceiling) is never memory-gated, so it is satisfied at once.
# Every check logs a timestamped line with the ceiling and usable memory, so a stuck wait names why.
#
# Prints one line, `wait-capacity: satisfied after <N>s` or `wait-capacity: timed out after <N>s`.
# Env: WAIT_CAPACITY_MAX (timeout clamp, seconds)
# Exit: 0 satisfied | 124 timed out | 2 usage error
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

die() {
  echo "wait-capacity.sh: $1" >&2
  exit 2
}

case "${1:-}" in
  -h | --help)
    sed -n '2,/^set /{/^set /!p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
esac

repo="${1:-}"
[ -n "$repo" ] || die "usage: wait-capacity.sh <repo-dir> [--timeout S] [--interval S]"
shift
timeout=1500
interval=30
while [ "$#" -gt 0 ]; do
  case "$1" in
    --timeout | --interval)
      [ "$#" -ge 2 ] || die "$1 needs a value"
      case "$1" in
        --timeout) timeout="$2" ;;
        --interval) interval="$2" ;;
      esac
      shift 2
      ;;
    *) die "unknown argument '$1'" ;;
  esac
done

max="${WAIT_CAPACITY_MAX:-1700}"
if [ "$timeout" -gt "$max" ]; then
  timeout="$max"
fi

start=$SECONDS
while true; do
  elapsed=$((SECONDS - start))
  # A failed or slow report (common while the host is short of memory) is retried, not fatal.
  if report="$(timeout 90 "$SCRIPT_DIR/wt-capacity.sh" "$repo" 2>&1)"; then
    ceiling="$(printf '%s\n' "$report" | awk -v repo="$repo" '$1 == repo {print $NF; exit}')"
    usable="$(printf '%s\n' "$report" | sed -n 's/^live_usable_gb=//p')"
    [ -n "$ceiling" ] || die "repo '$repo' not found in the capacity report"
  else
    echo "wait-capacity: $(date +%H:%M:%S) wt-capacity.sh failed (${elapsed}s), retrying: $(printf '%s' "$report" | tail -n 1)" >&2
    ceiling=0
    usable="?"
  fi
  echo "wait-capacity: $(date +%H:%M:%S) $repo stack_ceiling=$ceiling live_usable_gb=${usable:-?} (${elapsed}s)" >&2
  if [ "$ceiling" = "n/a" ] || [ "$ceiling" -gt 0 ] 2>/dev/null; then
    echo "wait-capacity: satisfied after ${elapsed}s"
    exit 0
  fi
  if [ "$elapsed" -ge "$timeout" ]; then
    echo "wait-capacity: timed out after ${elapsed}s"
    exit 124
  fi
  sleep "$interval"
done
