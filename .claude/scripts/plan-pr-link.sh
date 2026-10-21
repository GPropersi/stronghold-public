#!/usr/bin/env bash
#
# plan-pr-link.sh — record a pull-request URL in a plan's frontmatter (`pr_urls:`), so the plans
# dashboard shows the PR before it merges.
#
# Usage:
#   plan-pr-link.sh add <plan-file> <pr-url>
#
# Idempotent (a URL already linked changes nothing); only https://github.com/<owner>/<repo>/pull/<n> is accepted.
# Exit: 0 ok | 2 usage or bad URL | 3 missing file or no frontmatter. Full semantics: lib/plan_pr_link.py.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "${1:-}" in
  -h|--help)
    sed -n '2,/^set /{/^set /!p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0 ;;
esac

exec python3 "$SCRIPT_DIR/lib/plan_pr_link.py" "$@"
