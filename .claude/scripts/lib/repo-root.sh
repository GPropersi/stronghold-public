# shellcheck shell=bash
# repo-root.sh — shared, SOURCED helper that resolves which git checkout a path
# belongs to (a linked worktree at <repo>/.claude/worktrees/<slug>, or the primary clone).
# Never executed directly: no shebang path, no top-level set -e/exit/output.
#
# Consumers: .claude/hooks/post-edit-build.sh, list-allowlisted-entrypoints.sh,
# block-unsafe-push.sh, and .claude/scripts/git-cleanup.sh.
#
# Minimum supported git: >= 2.31 (when `rev-parse --path-format` was added). Per the
# worktree-native DD-7 decision there is NO older-git fallback for --path-format=absolute.
#
# Every function takes a path (a file path resolves via its directory), writes only
# to stdout, silences git's stderr, and returns 1 on failure so callers can fail open.
# Bash-3.2-safe (macOS): no mapfile, no associative arrays.
#
# Assumptions: repo_primary_root expects a standard non-bare layout (the common dir is
# <primary>/.git), and callers are not expected to set GIT_DIR, GIT_WORK_TREE or
# GIT_INDEX_FILE (they would override the -C resolution used throughout).

# _repo_root_dir <path> — the directory to run git in: <path> if a dir, else its dirname.
_repo_root_dir() {
  [ -n "${1:-}" ] || return 1
  if [ -d "$1" ]; then
    printf '%s\n' "$1"
  else
    dirname -- "$1"
  fi
}

# repo_toplevel <path> — root of the checkout (worktree or primary) containing <path>.
repo_toplevel() {
  local dir
  dir="$(_repo_root_dir "${1:-}")" || return 1
  git -C "$dir" rev-parse --show-toplevel 2>/dev/null || return 1
}

# repo_common_dir <path> — absolute path of the shared .git dir (same for all worktrees).
repo_common_dir() {
  local dir
  dir="$(_repo_root_dir "${1:-}")" || return 1
  git -C "$dir" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || return 1
}

# repo_primary_root <path> — the primary checkout (its basename is the repo name).
repo_primary_root() {
  local common
  common="$(repo_common_dir "${1:-}")" || return 1
  [ -n "$common" ] || return 1
  dirname -- "$common"
}

# repo_is_linked_worktree <path> — returns 0 iff <path> is inside a linked worktree.
repo_is_linked_worktree() {
  local dir gitdir common
  dir="$(_repo_root_dir "${1:-}")" || return 1
  gitdir="$(git -C "$dir" rev-parse --absolute-git-dir 2>/dev/null)" || return 1
  common="$(repo_common_dir "$dir")" || return 1
  [ "$gitdir" != "$common" ]
}

# is_stronghold_root <dir> — returns 0 iff <dir> is the stronghold: it equals
# $CLAUDE_PROJECT_DIR, or its origin is OWNER/stronghold (same regex as
# block-unsafe-push.sh and stronghold-sync.sh).
is_stronghold_root() {
  local dir="${1:-}" resolved project toplevel origin
  [ -n "$dir" ] && [ -d "$dir" ] || return 1
  resolved="$(CDPATH='' cd -- "$dir" 2>/dev/null && pwd -P)" || return 1
  if [ -n "${CLAUDE_PROJECT_DIR:-}" ]; then
    project="$(CDPATH='' cd -- "$CLAUDE_PROJECT_DIR" 2>/dev/null && pwd -P)" || project=""
    if [ -n "$project" ] && [ "$resolved" = "$project" ]; then
      return 0
    fi
  fi
  # The origin fallback only applies to a checkout root: `git -C <subdir>` walks up to the
  # parent repo, so a non-toplevel subdir must not inherit its origin.
  toplevel="$(repo_toplevel "$dir")" || return 1
  toplevel="$(CDPATH='' cd -- "$toplevel" 2>/dev/null && pwd -P)" || return 1
  [ "$toplevel" = "$resolved" ] || return 1
  origin="$(git -C "$dir" remote get-url origin 2>/dev/null)" || return 1
  printf '%s\n' "$origin" | grep -qE '[:/]OWNER/stronghold(\.git)?/?$'
}

# worktree_branches <path> — one branch name per line for every worktree of the repo.
worktree_branches() {
  local dir out key val
  dir="$(_repo_root_dir "${1:-}")" || return 1
  out="$(git -C "$dir" worktree list --porcelain 2>/dev/null)" || return 1
  while read -r key val; do
    if [ "$key" = "branch" ]; then
      printf '%s\n' "${val#refs/heads/}"
    fi
  done <<< "$out"
}
