#!/usr/bin/env bash
#
# pr-closing-check.sh — verify a PR closes exactly the issues it should (GitHub's own parse, not ours).
#
# Usage:
#   pr-closing-check.sh <repo-dir> <pr-number> [--expect N,N|none] [--fix]
#
# GitHub's keyword scanner closes #N on merge for any "close/fix/resolve #N" in the PR body, even in a
# negated sentence ("this does not close #39"). This compares the PR's closingIssuesReferences with
# --expect (default none). --fix rewrites the keyword in front of each unexpected reference to the word
# `reference`, edits the body and re-checks. Full semantics: lib/pr_closing_check.py --help.
#
# Env: STRONGHOLD_DIR        override the stronghold root (test seam)
#      PR_CLOSING_CHECK_GH   gh wrapper to run (default gh-bot.sh)
# Exit: 0 matches | 1 mismatch | 2 usage | 4 unavailable
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "${1:-}" in
  -h|--help)
    sed -n '2,/^set /{/^set /!p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0 ;;
esac

exec python3 "$SCRIPT_DIR/lib/pr_closing_check.py" "$@"
