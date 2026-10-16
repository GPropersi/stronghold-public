#!/usr/bin/env bash
#
# wt-resolve.sh — the Worktree Discipline "resolve once" algorithm as code.
#
# Usage:
#   wt-resolve.sh <repo> [--plan <file> | --branch <b> [--worktree <value>]] [--assert]
#
# <repo> is the sub-repo DIRECTORY name under the stronghold (or `stronghold`). Prints one JSON
# object {repo, branch, worktree_abs, worktree_rel, primary, kind, source, problems[]}:
#   kind    primary | linked | none   (none = branch checked out nowhere: run `make wt-new`)
#   source  plan-frontmatter (a trusted worktree: value) | porcelain (found by branch) |
#           current-branch (no branch given: the primary's current branch)
# A `worktree:` value is trusted only if `git worktree list --porcelain` lists that path on
# exactly refs/heads/<branch>; otherwise it is ignored and `stale-worktree` is reported.
# A prunable block or one whose path is missing counts as absent. `repo: stronghold` and
# default-branch plans never get a worktree. --plan reads branch:/worktree: from the plan and
# maps <repo> to its `Plans bucket` seam key (`plan-repo-mismatch` when the plan's repo: differs).
# --assert also checks `git branch --show-current` of the result equals the branch.
#
# Env: STRONGHOLD_DIR  override the stronghold root (test seam; default: this script's checkout)
# problems: stale-worktree (also for a worktree: value on a stronghold/default-branch plan),
#   plan-repo-mismatch, assert-skipped-no-worktree (--assert with kind=none: nothing to check, exit 0)
# Exit: 0 ok | 2 usage | 3 --assert mismatch | 4 repo has no usable git, or --plan and no `Plans bucket` key
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STRONGHOLD="${STRONGHOLD_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
# shellcheck source=lib/seam.sh
. "$SCRIPT_DIR/lib/seam.sh"

usage_error() {
  echo "wt-resolve.sh: $1" >&2
  exit 2
}

repo=""
plan=""
branch=""
branch_set=0
worktree=""
worktree_set=0
assert=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help)
      sed -n '2,/^set /{/^set /!p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    --assert) assert=1 ;;
    --plan)
      [ "$#" -ge 2 ] || usage_error "--plan needs a file"
      plan="$2"; shift ;;
    --branch)
      [ "$#" -ge 2 ] || usage_error "--branch needs a value"
      branch="$2"; branch_set=1; shift ;;
    --worktree)
      [ "$#" -ge 2 ] || usage_error "--worktree needs a value"
      worktree="$2"; worktree_set=1; shift ;;
    -*) usage_error "unknown option '$1'" ;;
    *)
      [ -z "$repo" ] || usage_error "unexpected argument '$1'"
      repo="$1" ;;
  esac
  shift
done

[ -n "$repo" ] || usage_error "usage: wt-resolve.sh <repo> [--plan <file> | --branch <b> [--worktree <value>]] [--assert]"
if [ -n "$plan" ] && [ "$branch_set" -eq 1 ]; then
  usage_error "--plan and --branch are mutually exclusive"
fi
if [ "$worktree_set" -eq 1 ] && [ "$branch_set" -eq 0 ]; then
  usage_error "--worktree requires --branch"
fi
if [ -n "$plan" ] && [ ! -f "$plan" ]; then
  usage_error "plan file not found: $plan"
fi

if [ "$repo" = "stronghold" ]; then
  primary="$STRONGHOLD"
else
  primary="$STRONGHOLD/$repo"
fi
if [ ! -e "$primary/.git" ]; then
  echo "wt-resolve.sh: $primary is not a git checkout" >&2
  exit 4
fi

bucket=""
if [ -n "$plan" ]; then
  if ! bucket="$(seam_plans_bucket "$STRONGHOLD" "$repo")"; then
    exit 4
  fi
fi

default_branch="$(seam_first_token "$primary/CLAUDE.md" "Default branch")"
[ -n "$default_branch" ] || default_branch="main"

args=(--stronghold "$STRONGHOLD" --repo "$repo" --primary "$primary" --default-branch "$default_branch")
[ -z "$plan" ] || args+=(--plan "$plan" --bucket "$bucket")
[ "$branch_set" -eq 0 ] || args+=(--branch "$branch")
[ "$worktree_set" -eq 0 ] || args+=(--worktree "$worktree")
[ "$assert" -eq 0 ] || args+=(--assert)

exec python3 "$SCRIPT_DIR/lib/wt_resolve.py" "${args[@]}"
