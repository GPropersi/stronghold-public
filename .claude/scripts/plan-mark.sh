#!/usr/bin/env bash
#
# plan-mark.sh — stamp a plan step's start or finish with the real clock time (`**Started:**` / `**Done:**`).
#
# Usage:
#   plan-mark.sh <repo>/<plan> start|done <N>
#
# Idempotent (first write wins); writes are locked (PLAN_MARK_LOCK) and atomic. Env: PLANS_DIR (test seam).
# Exit: 0 ok | 2 usage | 3 malformed existing stamp | 4 plan or step not found | 5 ambiguous plan name |
# 6 lock timeout or write error. Full semantics: lib/plan_mark.py.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "${1:-}" in
  -h|--help)
    sed -n '2,/^set /{/^set /!p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0 ;;
esac

exec python3 "$SCRIPT_DIR/lib/plan_mark.py" "$@"
