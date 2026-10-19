#!/usr/bin/env bash
#
# plan-lifecycle.sh — deterministic open -> completed lifecycle of the central plans store.
#
# Usage:
#   plan-lifecycle.sh complete <repo> <topic> [--dry-run]       verify finished, git mv open/ -> completed/, close its issues
#   plan-lifecycle.sh close-issues <repo> [<topic>] [--dry-run] close the open issue of every completed plan
#   plan-lifecycle.sh audit [<repo>]                            report completed plans with open issues + open topics with no plan
#   plan-lifecycle.sh sweep [<repo>] [--dry-run]                move plan-less (review-only) open topics to completed/
#   plan-lifecycle.sh backfill [--dry-run] [--only <rule>] [--force]  migrate the whole store to the plan schema (rules a-g)
#   plan-lifecycle.sh review-topic <repo> <name>                create/reuse the topic folder for a plan-less push review
#
# <repo> is a sub-repo DIRECTORY name under the stronghold (or `stronghold`); the plans bucket comes only
# from its `Plans bucket` seam key. Run from ~/code. Full semantics: lib/plan_lifecycle.py --help.
#
# Env: STRONGHOLD_DIR      override the stronghold root (test seam)
#      PLAN_LIFECYCLE_GH   gh wrapper to run (default gh-bot.sh)
# Exit: 0 ok | 1 audit drift | 2 usage | 3 topic not finishable | 4 unavailable
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "${1:-}" in
  -h|--help)
    sed -n '2,/^set /{/^set /!p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0 ;;
esac

exec python3 "$SCRIPT_DIR/lib/plan_lifecycle.py" "$@"
