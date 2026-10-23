#!/usr/bin/env bash
#
# stack-reap.sh — stop Docker stacks of worktrees that have been idle for at least a minimum time.
#
# Usage:
#   stack-reap.sh [--min-idle-hours H] [--dry-run]
#
#   --min-idle-hours   minimum idle time before a stack is stopped (default 6, or $STACK_REAP_MIN_IDLE_HOURS)
#   --dry-run          log what would be reaped, stop nothing
#
# Only `docker stop` of running containers in worktree stacks (*/.claude/worktrees/*): nothing is removed,
# so no code or data is lost and `make up` restarts the stack. Skipped (and logged with the reason): hubs,
# primary clones, non-worktree projects, stacks with a live process in the worktree, a queued/running hostq
# job, or idle time under the minimum. Every decision goes to ~/.local/state/hostq/reap.log
# ($STACK_REAP_LOG) with UTC time, epoch, project, idle seconds, rc and duration.
# Exit: 0 ok | 1 a docker stop failed
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "${1:-}" in
  -h|--help)
    sed -n '2,/^# Exit:/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0 ;;
esac
exec python3 "$SCRIPT_DIR/lib/host_stacks.py" reap "$@"
