#!/usr/bin/env bash
#
# plan-pr-sync.sh — record merged/closed PRs of every plan (`pr_done:` frontmatter), so the plans
# dashboard can show completed plans whose PRs are still in review.
#
# Usage:
#   plan-pr-sync.sh [--dry-run] [<plans-dir>]
#
# Reads each plan's `pr_urls:`, asks GitHub (via gh-bot.sh, inside the matching sub-repo checkout) for
# the state of every PR not yet in `pr_done:`, and adds MERGED or CLOSED ones. Idempotent; an unreadable
# PR is logged to stderr and stays "in review". Run from ~/code, then stronghold-sync.sh to publish.
# Exit: 0 ok | 2 usage. Full semantics: lib/plan_pr_sync.py.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "${1:-}" in
  -h|--help)
    sed -n '2,/^set /{/^set /!p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0 ;;
esac

exec python3 "$SCRIPT_DIR/lib/plan_pr_sync.py" "$@"
