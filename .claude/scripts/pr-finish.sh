#!/usr/bin/env bash
#
# pr-finish.sh — the label / milestone / project-board / assignee / reviewer plumbing of /git-push
# Step 9, as code. Run with cwd INSIDE the target sub-repo checkout (gh-bot.sh picks the App
# installation from the cwd's origin); it reads that checkout's own CLAUDE.md seam.
#
# Usage:
#   pr-finish.sh <pr-number> [--labels a,b] [--milestone <title>]
#
# --labels / --milestone are the model's choices; every name is checked against the repo's real
# label and milestone sets BEFORE any change, and an unknown name is refused (exit 3, valid names
# listed). Then: labels and milestone are applied (skipped when already present), the PR is added
# to the seam's `GitHub project board` (resolved by exact title, org then user variant) with
# Status "In progress" (a PR already on the board is left untouched), the seam's Bot identity is
# assigned (the documented FORBIDDEN response is a logged skip) and the seam's `PR reviewer` is
# requested (skipped on n/a). A repo with no board, bot or reviewer skips that step with a note.
#
# Env: PF_GH          the gh binary (default: gh-bot.sh next to this script; tests point it at a stub)
#      STRONGHOLD_DIR override the stronghold root that holds the GraphQL files (test seam)
# Exit: 0 success (logged skips are success) | 2 usage | 3 label or milestone not in the repo's set |
#   4 could not run (no seam, no Repo slug or no origin in the cwd checkout, or a gh call failed)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STRONGHOLD="${STRONGHOLD_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
# shellcheck source=lib/seam.sh
. "$SCRIPT_DIR/lib/seam.sh"

usage_error() {
  echo "pr-finish.sh: $1" >&2
  exit 2
}

pr=""
labels=""
milestone=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help)
      sed -n '2,/^set /{/^set /!p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    --labels)
      [ "$#" -ge 2 ] || usage_error "--labels needs a value"
      labels="$2"; shift ;;
    --milestone)
      [ "$#" -ge 2 ] || usage_error "--milestone needs a value"
      milestone="$2"; shift ;;
    -*) usage_error "unknown option '$1'" ;;
    *)
      [ -z "$pr" ] || usage_error "unexpected argument '$1'"
      pr="$1" ;;
  esac
  shift
done

[ -n "$pr" ] || usage_error "usage: pr-finish.sh <pr-number> [--labels a,b] [--milestone <title>]"
case "$pr" in
  *[!0-9]*) usage_error "PR number must be numeric: '$pr'" ;;
esac

if ! root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  echo "pr-finish.sh: not inside a git checkout" >&2
  exit 4
fi
if [ ! -f "$root/CLAUDE.md" ]; then
  echo "pr-finish.sh: no CLAUDE.md seam in $root" >&2
  exit 4
fi
if ! git -C "$root" remote get-url origin >/dev/null 2>&1; then
  echo "pr-finish.sh: $root has no origin remote" >&2
  exit 4
fi
slug="$(seam_first_token "$root/CLAUDE.md" "Repo slug")"
if [ -z "$slug" ]; then
  echo "pr-finish.sh: missing seam key \"Repo slug\" in $root/CLAUDE.md" >&2
  exit 4
fi

export PF_GH="${PF_GH:-$SCRIPT_DIR/gh-bot.sh}"

exec python3 "$SCRIPT_DIR/lib/pr_finish.py" \
  "--pr=$pr" "--slug=$slug" \
  "--graphql-dir=$STRONGHOLD/.claude/skills/git-push/references/graphql" \
  "--labels=$labels" "--milestone=$milestone" \
  "--board=$(seam_first_token "$root/CLAUDE.md" "GitHub project board")" \
  "--bot=$(seam_first_token "$root/CLAUDE.md" "Bot identity")" \
  "--reviewer=$(seam_first_token "$root/CLAUDE.md" "PR reviewer")"
