#!/usr/bin/env bash
# wt-singleton.sh — locking wrapper for `Worktree policy: singleton-runtime` repos.
#
#   wt-singleton.sh run <checkout-dir> <target> [make-args...]
#   wt-singleton.sh status [<repo>]
#   wt-singleton.sh release [--force] <repo>
#
# `run` starts a guarded make target (one named in `Worktree guarded targets`) only when no other
# checkout of the same repo holds a live instance. The block-worktree-live-resources.sh hook denies
# the bare `make <guarded>` and prints this wrapper's absolute path. The lock is a short
# machine-wide fcntl.flock plus a persistent holder record (lib/wt_singleton.py); the holder is live
# while the wrapper's pid is alive or a compose container's working_dir is under the holder's
# checkout. `release --force` is deliberately NOT allowlisted: it always prompts the user.
#
# WT_LOCK_DIR overrides the lock directory (default
# ${XDG_STATE_HOME:-$HOME/.local/state}/stronghold/singleton). Policy files are read through
# wtg_policy_md, the same primary-wins resolution the hook uses.
set -euo pipefail

# Own directory first (independent of the cwd, so the relative and the absolute allowlisted forms
# both work). A symlinked wrapper file is not resolved, as in wt.sh and wt-lib.sh.
_WT_SINGLETON_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || {
  echo "wt-singleton.sh: cannot resolve its own directory" >&2
  exit 1
}
# shellcheck source=lib/repo-root.sh
. "$_WT_SINGLETON_DIR/lib/repo-root.sh"
# shellcheck source=lib/seam.sh
. "$_WT_SINGLETON_DIR/lib/seam.sh"
# shellcheck source=lib/worktree-guard.sh
. "$_WT_SINGLETON_DIR/lib/worktree-guard.sh"

LOCK_DIR="${WT_LOCK_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/stronghold/singleton}"
APPROVAL_NOTE="release --force is not auto-approved and needs the user's approval"

# _wts_helper <subcommand> <args...> — the python lock helper (_WT_GUARD_LIB_DIR comes from the lib).
_wts_helper() {
  python3 "$_WT_GUARD_LIB_DIR/wt_singleton.py" "$@"
}

_usage_error() {
  echo "usage: wt-singleton.sh run <checkout-dir> <target> [make-args...] | status [<repo>] | release [--force] <repo>" >&2
  echo "wt-singleton.sh: $1" >&2
  exit 2
}

# _check_goal <goal> <guarded-globs> — refuse anything make could read as an option, a variable
# assignment or a path (-f/--eval/-C, VAR=x, a/b), and any goal outside the guarded globs. `run:*`
# is allowlisted and unsandboxed, so every word after `run` must be vetted here.
_check_goal() {
  local goal="$1" guarded="$2"
  case "$goal" in
    '' | -* | *=* | */* | *[[:space:]]*)
      echo "wt-singleton.sh: refusing: '$goal' is not a plain make target name (no options, assignments, paths or spaces)" >&2
      exit 1
      ;;
  esac
  if ! wtg_glob_match "$goal" "$guarded"; then
    echo "wt-singleton.sh: refusing: '$goal' is not in Worktree guarded targets (${guarded:-<empty>}); this wrapper only runs guarded targets" >&2
    exit 1
  fi
}

cmd_run() {
  [ "$#" -ge 2 ] || _usage_error "run needs <checkout-dir> and <target>"
  local checkout="$1" target="$2" top root md policy guarded primary repo branch rc make_rc goal
  shift 2
  top=$(repo_toplevel "$checkout") || {
    echo "wt-singleton.sh: $checkout is not inside a git checkout" >&2
    exit 1
  }
  # Only checkouts under the project root: an unrelated repo's own CLAUDE.md and Makefile are not trusted.
  root=$(CDPATH='' cd -- "${CLAUDE_PROJECT_DIR:-$PWD}" && pwd -P) || {
    echo "wt-singleton.sh: cannot resolve the project root" >&2
    exit 1
  }
  case "$top/" in
    "$root"/*) ;;
    *)
      echo "wt-singleton.sh: refusing: $top is not under the project root $root" >&2
      exit 1
      ;;
  esac
  md=$(wtg_policy_md "$top") || {
    echo "wt-singleton.sh: cannot resolve the policy file for $top" >&2
    exit 1
  }
  policy=$(seam_raw "$md" 'Worktree policy')
  guarded=$(seam_raw "$md" 'Worktree guarded targets')
  if [ "$policy" != "singleton-runtime" ]; then
    echo "wt-singleton.sh: refusing: Worktree policy of $top is '${policy:-<unset>}', not singleton-runtime" >&2
    exit 1
  fi
  _check_goal "$target" "$guarded"
  for goal in "$@"; do
    _check_goal "$goal" "$guarded"
  done
  primary=$(repo_primary_root "$top") || {
    echo "wt-singleton.sh: cannot resolve the primary clone of $top" >&2
    exit 1
  }
  repo=$(basename -- "$primary")
  branch=$(git -C "$top" branch --show-current) || {
    echo "wt-singleton.sh: cannot read the branch of $top" >&2
    exit 1
  }

  # `$$` is the wrapper's own pid: the holder record must outlive the helper process.
  if _wts_helper acquire --lock-dir "$LOCK_DIR" --repo "$repo" --worktree "$top" \
    --branch "${branch:-<detached>}" --pid $$ --target "$target"; then rc=0; else rc=$?; fi
  case "$rc" in
    0) ;;
    3)
      echo "wt-singleton.sh: $repo already has a live instance (holder above); $APPROVAL_NOTE" >&2
      exit 1
      ;;
    4)
      echo "wt-singleton.sh: the singleton lock for $repo is busy; try again shortly" >&2
      exit 1
      ;;
    *)
      echo "wt-singleton.sh: could not acquire the singleton lock for $repo (helper exit $rc)" >&2
      exit 1
      ;;
  esac

  if make -C "$top" -- "$target" "$@"; then make_rc=0; else make_rc=$?; fi

  # Drop the record only when nothing is running any more (a foreground target that exited, or a
  # failed start). The wrapper's own pid is still alive here, hence --docker-only.
  if _wts_helper is-live --lock-dir "$LOCK_DIR" --repo "$repo" --docker-only; then rc=0; else rc=$?; fi
  case "$rc" in
    5)
      if ! _wts_helper release --lock-dir "$LOCK_DIR" --repo "$repo" --expect-pid $$; then
        echo "wt-singleton.sh: warning: could not release the holder record for $repo" >&2
      fi
      ;;
    0) ;;
    *)
      echo "wt-singleton.sh: warning: liveness check failed (exit $rc); keeping the holder record for $repo" >&2
      ;;
  esac
  exit "$make_rc"
}

cmd_status() {
  local root dir name
  if [ "$#" -ge 1 ]; then
    [ "$#" -eq 1 ] || _usage_error "status takes at most one <repo>"
    case "$1" in -*) _usage_error "unknown status option: $1" ;; esac
    _wts_helper status --lock-dir "$LOCK_DIR" --repo "$1"
    return
  fi
  root="${CLAUDE_PROJECT_DIR:-$PWD}"
  for dir in "$root"/*/; do
    [ -e "${dir}.git" ] || continue
    [ "$(seam_raw "${dir}CLAUDE.md" 'Worktree policy')" = "singleton-runtime" ] || continue
    name=$(basename -- "$dir")
    if ! _wts_helper status --lock-dir "$LOCK_DIR" --repo "$name"; then
      echo "wt-singleton.sh: warning: status failed for $name" >&2
    fi
  done
  exit 0
}

cmd_release() {
  local arg repo="" force=()
  for arg in "$@"; do
    case "$arg" in
      --force) force=(--force) ;;
      -*) _usage_error "unknown release option: $arg" ;;
      *)
        [ -z "$repo" ] || _usage_error "release takes one <repo>"
        repo="$arg"
        ;;
    esac
  done
  [ -n "$repo" ] || _usage_error "release needs a <repo> (it never guesses which lock to drop)"
  _wts_helper release --lock-dir "$LOCK_DIR" --repo "$repo" ${force[@]+"${force[@]}"}
}

case "${1:-}" in
  run) shift; cmd_run "$@" ;;
  status) shift; cmd_status "$@" ;;
  release) shift; cmd_release "$@" ;;
  *) _usage_error "unknown or missing subcommand: ${1:-<none>}" ;;
esac
