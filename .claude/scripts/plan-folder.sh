#!/usr/bin/env bash
#
# plan-folder.sh — pick the central-store plan folder for a pushed branch (deterministic scoring).
#
# Usage:
#   plan-folder.sh <repo> <branch>
#
# <repo> is the sub-repo DIRECTORY name under the stronghold (or `stronghold`); it is mapped to the
# plans bucket ONLY through the `Plans bucket` seam key (no slug or directory fallback). Prints the
# JSON the /git-push skill parses: {decision: match|ambiguous|no_match, branch, meaningful_tokens,
# plan_dir (match only), candidates[], suggested_new_folder}. A plan whose frontmatter `branch:`
# equals <branch> is an exact match before any token scoring; otherwise the scoring of
# lib/plan_folder.py scoring applies (open/ beats completed/ on a tie).
#
# Env: STRONGHOLD_DIR  override the stronghold root (test seam; default: this script's checkout)
# Exit: 0 ok | 2 usage | 4 the repo's seam has no `Plans bucket` key
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STRONGHOLD="${STRONGHOLD_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
# shellcheck source=lib/seam.sh
. "$SCRIPT_DIR/lib/seam.sh"

usage_error() {
  echo "plan-folder.sh: $1" >&2
  exit 2
}

repo=""
branch=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help)
      sed -n '2,/^set /{/^set /!p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    -*) usage_error "unknown option '$1'" ;;
    *)
      if [ -z "$repo" ]; then
        repo="$1"
      elif [ -z "$branch" ]; then
        branch="$1"
      else
        usage_error "unexpected argument '$1'"
      fi ;;
  esac
  shift
done

[ -n "$repo" ] && [ -n "$branch" ] || usage_error "usage: plan-folder.sh <repo> <branch>"

if ! bucket="$(seam_plans_bucket "$STRONGHOLD" "$repo")"; then
  exit 4
fi

exec python3 "$SCRIPT_DIR/lib/plan_folder.py" --stronghold "$STRONGHOLD" --bucket "$bucket" --branch "$branch"
