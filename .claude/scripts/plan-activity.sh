#!/usr/bin/env bash
#
# plan-activity.sh — time-ordered plan activity feed (git history of plans/ merged with step Started/Done stamps).
#
# Usage:
#   plan-activity.sh [--repo R] [--limit N] [--json] [--plans-dir D]
#
# Newest first, no limit by default. Env: PLANS_DIR (default <stronghold>/plans).
# Exit: 0 ok | 2 usage | 4 unknown repo bucket. Full semantics: lib/plan_activity.py.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "${1:-}" in
  -h|--help)
    sed -n '2,/^set /{/^set /!p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0 ;;
esac

exec python3 "$SCRIPT_DIR/lib/plan_activity.py" "$@"
