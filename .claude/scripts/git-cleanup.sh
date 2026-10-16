#!/usr/bin/env bash
# git-cleanup.sh — list stale local branches and delete them after confirmation.
#
# Backs the `git cleanup` alias that the gmas skill/alias calls. Kept as a
# tracked stronghold script (rather than an inline ~/.gitconfig alias) so the
# logic is reviewable and travels to a fresh machine with the repo — the
# gitconfig only needs the one-line alias:
#
#   [alias]
#       cleanup = !~/code/.claude/scripts/git-cleanup.sh
#
# Output contract (parsed by the gmas skill):
#   * an optional "Merged worktrees:" block FIRST, one "  <worktree path>  <branch>"
#     line per linked worktree whose branch's upstream is gone (the PR merged and
#     `git fetch -p` pruned it). The primary checkout and the tree the script is run
#     from are never listed, and neither is a never-pushed branch (no upstream).
#     Locked worktrees are not listed (a non-force `git worktree remove` fails on them).
#     The script only reports these — it never removes a worktree; gmas does.
#     gmas removes a merged worktree by invoking `<stronghold>/.claude/scripts/wt.sh rm
#     <repo> <branch>` directly, with `<stronghold>` resolved at runtime (never the static
#     literal `~/code/...`) so the call matches the `__STRONGHOLD__`-substituted sandbox
#     exclusion. A human does it manually with `make wt-rm REPO=<repo> BRANCH=<branch>`
#     (same script, different entry point).
#   * then "No branches to clean up." when there are no candidates, or
#   * a "Will delete:" line, one two-space-indented branch name per line,
#     then a "Proceed?" prompt read from stdin.
#   So `echo n | git cleanup` previews the candidate list without deleting.
#
# Worktrees: stale worktree records are pruned first (`git worktree prune`), and a
# branch checked out in ANY worktree (primary included) is never a candidate —
# `git branch -D` would refuse it and abort the whole batch. That prune is the one
# side effect of a preview run (it drops records of worktrees whose directory is
# missing); the script otherwise only reports and never removes or deletes anything
# without confirmation.
#
# A branch is a candidate when it is neither the default branch nor the current
# branch, and either:
#   * its upstream is gone — `git fetch -p` pruned the remote-tracking ref after
#     the PR merged (the normal post-merge case), or
#   * it has no upstream at all AND no refs/remotes/origin/<branch> exists.
#
# NOTE on that second category: a brand-new local branch you have not pushed yet
# also matches it. Deletion uses `git branch -D`, so confirm the list before
# answering y — gmas additionally confirms branch-by-branch.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/repo-root.sh
. "$SCRIPT_DIR/lib/repo-root.sh"

# Default branch: prefer main, fall back to master.
DEFAULT_BRANCH=main
if ! git show-ref --verify --quiet refs/heads/main; then
  if git show-ref --verify --quiet refs/heads/master; then
    DEFAULT_BRANCH=master
  fi
fi

# Drop records of worktrees whose directory is gone, so their branches are no
# longer held and the exclusion below only sees live checkouts.
git worktree prune 2>/dev/null || true
wt_branches="$(worktree_branches . || true)"

CURRENT_BRANCH="$(git branch --show-current 2>/dev/null || true)"

candidates=()
while IFS='|' read -r branch upstream track; do
  [ -n "$branch" ] || continue
  [ "$branch" != "$DEFAULT_BRANCH" ] || continue
  [ "$branch" != "$CURRENT_BRANCH" ] || continue
  # Checked out in some worktree: not deletable (exact whole-line match).
  ! grep -qxF -- "$branch" <<< "$wt_branches" || continue

  if [ "$track" = "[gone]" ]; then
    candidates+=("$branch")
  elif [ -z "$upstream" ] && ! git show-ref --verify --quiet "refs/remotes/origin/${branch}"; then
    candidates+=("$branch")
  fi
done < <(git for-each-ref --format='%(refname:short)|%(upstream)|%(upstream:track)' refs/heads/)

# Linked worktrees whose branch upstream is gone. Skips the first porcelain block
# (the primary), the tree we are running in, detached and branchless entries, and
# branches with no upstream at all (a fresh, never-pushed worktree branch).
current_top="$(repo_toplevel . 2>/dev/null || true)"
merged_worktrees=()
block_index=0
wt_path=""
wt_branch=""
wt_locked=0
while IFS= read -r line; do
  case "$line" in
    "worktree "*)
      wt_path="${line#worktree }"
      wt_branch=""
      wt_locked=0
      block_index=$((block_index + 1))
      ;;
    "branch refs/heads/"*)
      wt_branch="${line#branch refs/heads/}"
      ;;
    "locked" | "locked "*)
      wt_locked=1
      ;;
    "")
      if [ "$block_index" -gt 1 ] && [ -n "$wt_path" ] && [ -n "$wt_branch" ] \
        && [ "$wt_locked" -eq 0 ] \
        && [ "$wt_path" != "$current_top" ] \
        && [ "$(git for-each-ref --format='%(upstream:track)' "refs/heads/${wt_branch}")" = "[gone]" ]; then
        merged_worktrees+=("  ${wt_path}  ${wt_branch}")
      fi
      wt_path=""
      wt_branch=""
      wt_locked=0
      ;;
  esac
done < <(git worktree list --porcelain 2>/dev/null || true; echo)

if [ "${#merged_worktrees[@]}" -gt 0 ]; then
  echo "Merged worktrees:"
  printf '%s\n' "${merged_worktrees[@]}"
fi

if [ "${#candidates[@]}" -eq 0 ]; then
  echo "No branches to clean up."
  exit 0
fi

echo "Will delete:"
for branch in "${candidates[@]}"; do
  echo "  $branch"
done

printf 'Proceed? [y/N] '
read -r answer || answer=""

case "$answer" in
  y | Y | yes | YES)
    git branch -D "${candidates[@]}"
    ;;
  *)
    echo "Aborted. No branches deleted."
    ;;
esac
