#!/usr/bin/env bash
#
# host-status.sh — which Docker stack belongs to which worktree and branch, and how long it has been idle.
#
# Usage:
#   host-status.sh [--json]
#
# One row per compose project: running/total containers, idle time (since the newest file edit, git
# commit/stage, hostq job or live process in its worktree), the signal that set it, branch and worktree.
# Read-only. Reaping idle stacks is stack-reap.sh; the signals are documented in lib/host_stacks.py.
# Exit: 0 ok | non-zero docker unreachable
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "${1:-}" in
  -h|--help)
    sed -n '2,/^# Exit:/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0 ;;
esac
exec python3 "$SCRIPT_DIR/lib/host_stacks.py" status "$@"
