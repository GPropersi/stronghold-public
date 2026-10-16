#!/usr/bin/env bash
#
# wt.sh — create, list and remove a sub-repo worktree at <repo>/.claude/worktrees/<slug>.
#
# Usage:
#   wt.sh new [--init] <repo> <branch>   create the worktree for <branch> (make wt-new REPO=.. BRANCH=.. [INIT=1])
#   wt.sh ls [<repo>]           list every worktree (primary included), all repos or one
#   wt.sh rm [--init] <repo> <branch>    remove the worktree checked out on <branch> (never --force)
#   wt.sh -h | --help           this text
#
# A thin dispatcher. <slug> = sanitize(<branch>), computed once by `new`. When the repo's own
# Makefile defines `worktree-new` / `worktree-rm` that target does the work (new: run from the
# primary with name=<slug> b=<branch>; rm: run inside the worktree). Otherwise a generic path
# runs, driven by the repo's `## Claude Config` keys: Worktree link | setup | teardown | ports |
# policy. A repo with neither is refused (exit 1, nothing created). `rm` resolves the worktree
# by BRANCH from `git worktree list --porcelain`, never by re-sanitizing.
#
# --init (make wt-new ... INIT=1) bootstraps a repo with no worktree support yet (no owned target,
# no `Worktree *` keys): the generic path runs with every key empty, i.e. a plain worktree and
# branch (based on origin/<default>) plus an identity-only .worktree.env, with a warning. It lets a
# plan that ADDS worktree support run on its own branch. A repo that already has support ignores
# the flag (warning) and takes its normal path. `rm --init` likewise lets such a worktree be removed.
#
# Generic `new` also appends the paths it creates (/.worktree.env and each link) to the repo's
# shared .git/info/exclude (local, never tracked) so a clean worktree can be removed non-force.
#
# Env:
#   STRONGHOLD_DIR   override the stronghold root (test seam; default: this script's checkout)
#
# Exit: 0 ok, 1 refused or failed, 2 usage.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STRONGHOLD="${STRONGHOLD_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"

# shellcheck source=lib/repo-root.sh
. "$SCRIPT_DIR/lib/repo-root.sh"
# shellcheck source=lib/seam.sh
. "$SCRIPT_DIR/lib/seam.sh"
# shellcheck source=lib/wt-lib.sh
. "$SCRIPT_DIR/lib/wt-lib.sh"

# Set by _wt_preamble: the repo's primary dir, and 1 when its Makefile owns the lifecycle target.
REPO_DIR=""
OWNED=0
# 1 when --init was passed.
INIT=0
# Set by _wt_find_branch.
_WT_FOUND_PATH=""
_WT_FOUND_LOCKED=0
_WT_FOUND_PRIMARY=0

usage() {
  printf 'usage: wt.sh new [--init] <repo> <branch> | ls [<repo>] | rm [--init] <repo> <branch>\n' >&2
}

wt_die() {
  printf 'wt: %s\n' "$*" >&2
}

wt_warn() {
  printf 'wt: warning: %s\n' "$*" >&2
}

# _wt_repo_dir <repo> — set REPO_DIR; message + return 1 unless $STRONGHOLD/<repo> is a git repo.
# `.git` must exist inside the dir itself: `git -C` would walk up into the stronghold's own repo.
_wt_repo_dir() {
  case "${1:-}" in
    "" | . | .. | */*)
      wt_die "no such repo ${1:-}"
      return 1
      ;;
  esac
  if [ ! -e "$STRONGHOLD/$1/.git" ]; then
    wt_die "no such repo $1"
    return 1
  fi
  REPO_DIR="$STRONGHOLD/$1"
}

# _wt_owns_target <repo-dir> <target> — 0 iff the repo's root Makefile defines <target>.
_wt_owns_target() {
  [ -f "$1/Makefile" ] || return 1
  grep -Eq "^$2:" "$1/Makefile"
}

# _wt_has_seam_keys <repo-dir> — 0 iff any `Worktree *` key is set (not empty, not n/a).
_wt_has_seam_keys() {
  local key
  for key in link setup teardown ports policy "allowed targets" "guarded targets"; do
    if [ -n "$(seam_raw "$1/CLAUDE.md" "Worktree $key")" ]; then
      return 0
    fi
  done
  return 1
}

# _wt_preamble <owned-target> <repo> — the only thing `new` and `rm` share. Sets REPO_DIR/OWNED.
_wt_preamble() {
  local target="$1" repo="$2"
  _wt_repo_dir "$repo" || return 1
  OWNED=0
  if _wt_owns_target "$REPO_DIR" "$target"; then
    OWNED=1
    return 0
  fi
  if _wt_has_seam_keys "$REPO_DIR"; then
    if [ "$INIT" -eq 1 ]; then wt_warn "--init ignored: $repo already has worktree support"; fi
    return 0
  fi
  if [ "$INIT" -eq 1 ]; then
    wt_warn "$repo has no worktree support yet; --init makes a plain worktree (no links, setup, ports or compose isolation)"
    return 0
  fi
  wt_die "$repo has no worktree support (no worktree-new/worktree-rm target and no Worktree keys in ## Claude Config; --init bootstraps a plain worktree)"
  return 1
}

# _wt_find_branch <repo-dir> <branch> — resolve the worktree on <branch> from the porcelain
# listing into _WT_FOUND_PATH / _WT_FOUND_LOCKED / _WT_FOUND_PRIMARY (the first block is always
# the primary checkout). _WT_FOUND_PATH stays empty when none matches (detached ones never do).
_wt_find_branch() {
  local out line cur="" br="" locked=0 index=0
  _WT_FOUND_PATH=""
  _WT_FOUND_LOCKED=0
  _WT_FOUND_PRIMARY=0
  out="$(git -C "$1" worktree list --porcelain)" || return 1
  while IFS= read -r line; do
    case "$line" in
      "worktree "*)
        cur="${line#worktree }"
        br=""
        locked=0
        index=$((index + 1))
        ;;
      "branch "*) br="${line#branch }" ;;
      locked | "locked "*) locked=1 ;;
      "")
        if [ -n "$cur" ] && [ "$br" = "refs/heads/$2" ]; then
          _WT_FOUND_PATH="$cur"
          _WT_FOUND_LOCKED="$locked"
          if [ "$index" -eq 1 ]; then _WT_FOUND_PRIMARY=1; fi
          return 0
        fi
        cur=""
        ;;
    esac
  done <<EOF
$out

EOF
  return 0
}

# _wt_exclude_add <repo-dir> <pattern> — idempotently append to the repo's shared exclude file.
_wt_exclude_add() {
  local common file
  common="$(git -C "$1" rev-parse --path-format=absolute --git-common-dir)" || return 1
  file="$common/info/exclude"
  mkdir -p "$common/info"
  if [ -f "$file" ] && grep -qxF -- "$2" "$file"; then
    return 0
  fi
  if [ -s "$file" ] && [ -n "$(tail -c 1 "$file")" ]; then
    printf '\n' >> "$file"
  fi
  printf '%s\n' "$2" >> "$file"
}

# _wt_link_paths <raw> — normalised `Worktree link` paths, one per line (./ and trailing / dropped).
_wt_link_paths() {
  local rel
  # No globbing: the word split must not expand against the caller's cwd.
  set -f
  for rel in $1; do
    while [ "${rel#./}" != "$rel" ]; do rel="${rel#./}"; done
    while [ "${rel%/}" != "$rel" ]; do rel="${rel%/}"; done
    [ -n "$rel" ] && printf '%s\n' "$rel"
  done
  set +f
  return 0
}

# _wt_step_failed <step> <repo> <branch> <path> — report a failure after `worktree add`; never
# deletes. The hint names the manual fallback too: wt-rm itself can abort (teardown or compose
# down failing on the half-built worktree), and a plain non-force `git worktree remove` cannot.
_wt_step_failed() {
  wt_die "step \"$1\" failed; the worktree was left in place"
  printf 'wt: clean up with: make wt-rm REPO=%q BRANCH=%q\n' "$2" "$3" >&2
  printf 'wt: if wt-rm fails too, remove it by hand (non-force): git -C %q worktree remove %q\n' \
    "$REPO_DIR" "$4" >&2
}

# _wt_make_value <text> — escape `$` as `$$` so make hands the value through literally.
# make expands command-line variables whenever they are used, so a ref such as a$(CURDIR)z
# (valid to git) would otherwise be expanded by the repo's Makefile.
_wt_make_value() {
  printf '%s' "$1" | sed 's/\$/$$/g'
}

# _wt_check_branch <repo-dir> <branch> — 0 iff <branch> is a plain, valid git branch name.
# Runs before anything is delegated to make or git. `@{` is rejected because --branch would
# otherwise expand @{-1} style shorthands instead of validating the literal name.
_wt_check_branch() {
  case "$2" in
    -* | *'@{'*)
      wt_die "\"$2\" is not a valid git branch name"
      return 1
      ;;
  esac
  if ! git -C "$1" check-ref-format --branch "$2" > /dev/null 2>&1; then
    wt_die "\"$2\" is not a valid git branch name"
    return 1
  fi
}

cmd_new() {
  local repo="$1" branch="$2" slug
  _wt_preamble worktree-new "$repo" || return 1
  # Validate before anything is delegated: an owned target receives the branch through make.
  _wt_check_branch "$REPO_DIR" "$branch" || return 1
  slug="$(wt_slug_dns "$branch")" || return 1
  if [ "$slug" = "$repo" ]; then
    wt_die "branch $branch sanitizes to \"$slug\", the repo name; pick another branch name"
    return 1
  fi
  if [ "$OWNED" -eq 1 ]; then
    local owned_rc=0
    make -C "$REPO_DIR" worktree-new "name=$(_wt_make_value "$slug")" "b=$(_wt_make_value "$branch")" || owned_rc=$?
    return "$owned_rc"
  fi
  _wt_new_generic "$repo" "$branch" "$slug"
}

_wt_new_generic() {
  local repo="$1" branch="$2" slug="$3"
  local policy links setup ports default_branch primary_root path project
  local rel line base_ref

  # --- pre-flight: everything that can refuse runs before git touches anything ---
  policy="$(seam_raw "$REPO_DIR/CLAUDE.md" "Worktree policy")"
  case "$policy" in
    "" | full | build-only | singleton-runtime) ;;
    *)
      wt_die "Worktree policy \"$policy\" is invalid (expected full, build-only or singleton-runtime)"
      return 1
      ;;
  esac
  links="$(seam_raw "$REPO_DIR/CLAUDE.md" "Worktree link")"
  # A build-only repo never gets secrets linked in: drop the paths before they are validated,
  # created or excluded.
  if [ "$policy" = "build-only" ] && [ -n "$links" ]; then
    wt_warn "Worktree link skipped: build-only policy never links secrets"
    links=''
  fi
  setup="$(seam_raw "$REPO_DIR/CLAUDE.md" "Worktree setup")"
  ports="$(seam_raw "$REPO_DIR/CLAUDE.md" "Worktree ports")"
  default_branch="$(seam_first_token "$REPO_DIR/CLAUDE.md" "Default branch")"
  default_branch="${default_branch:-main}"

  while IFS= read -r rel; do
    [ -n "$rel" ] || continue
    case "/$rel/" in
      //* | */../* | /.git/ | /.git/*)
        wt_die "Worktree link path \"$rel\" must be relative, inside the repo and not .git (no absolute paths or ..)"
        return 1
        ;;
    esac
  done <<EOF
$(_wt_link_paths "$links")
EOF

  primary_root="$(wt_primary_root "$REPO_DIR")" || return 1
  path="$primary_root/.claude/worktrees/$slug"
  project="$(wt_project_name "$repo" "$slug")" || return 1

  if [ -e "$path" ] || [ -L "$path" ]; then
    wt_die "worktree directory $path already exists (slug \"$slug\")"
    return 1
  fi
  while IFS= read -r line; do
    case "$line" in
      "worktree "*)
        if [ "$(basename -- "${line#worktree }")" = "$slug" ]; then
          wt_die "another worktree (${line#worktree }) already uses the slug \"$slug\""
          return 1
        fi
        ;;
    esac
  done <<EOF
$(git -C "$REPO_DIR" worktree list --porcelain)
EOF

  # Compose-project uniqueness: only meaningful when the repo declares ports, and skipped when
  # docker is absent (the port resolver itself still fails loudly in that case).
  if [ -n "$ports" ] && command -v docker > /dev/null 2>&1; then
    local projects
    projects="$(docker compose ls --all --format json)" || {
      wt_die "docker compose ls failed; cannot verify that compose project $project is free"
      return 1
    }
    # A case match, not `printf | grep -q`: grep exiting early would SIGPIPE printf and turn
    # a hit into status 141 under pipefail on large listings.
    case "$projects" in
      *"\"Name\":\"$project\""*)
        wt_die "compose project \"$project\" is already in use (docker compose ls); stop it or pick another branch name"
        return 1
        ;;
    esac
  fi

  # --- create the worktree ---
  if ! git -C "$REPO_DIR" check-ignore -q ".claude/worktrees/$slug/x" 2> /dev/null; then
    wt_warn "$repo's .gitignore lacks .claude/worktrees/; add it so worktrees do not show as untracked"
  fi
  if git -C "$REPO_DIR" show-ref --verify --quiet "refs/heads/$branch"; then
    git -C "$REPO_DIR" worktree add "$path" "$branch" || return 1
  elif git -C "$REPO_DIR" show-ref --verify --quiet "refs/remotes/origin/$branch"; then
    git -C "$REPO_DIR" worktree add --track -b "$branch" "$path" "origin/$branch" || return 1
  else
    base_ref="origin/$default_branch"
    if ! git -C "$REPO_DIR" show-ref --verify --quiet "refs/remotes/$base_ref"; then
      wt_die "$base_ref not found in $repo; run git fetch there first"
      return 1
    fi
    git -C "$REPO_DIR" worktree add --no-track -b "$branch" "$path" "$base_ref" || return 1
  fi

  # --- link, setup, ports/env file (each failure names its step and the cleanup command) ---
  if ! _wt_exclude_add "$REPO_DIR" "/.worktree.env"; then
    _wt_step_failed "exclude" "$repo" "$branch" "$path"
    return 1
  fi
  while IFS= read -r rel; do
    [ -n "$rel" ] || continue
    if [ ! -e "$primary_root/$rel" ] && [ ! -L "$primary_root/$rel" ]; then
      wt_warn "Worktree link source $rel does not exist in the primary checkout; skipped"
      continue
    fi
    if [ -e "$path/$rel" ] || [ -L "$path/$rel" ]; then
      wt_warn "Worktree link $rel already exists in the worktree; left alone"
      continue
    fi
    if ! { mkdir -p "$(dirname -- "$path/$rel")" \
      && ln -s "$primary_root/$rel" "$path/$rel" \
      && _wt_exclude_add "$REPO_DIR" "/$(printf '%s' "$rel" | sed 's/[][\*?]/\\&/g')"; }; then
      _wt_step_failed "link" "$repo" "$branch" "$path"
      return 1
    fi
  done <<EOF
$(_wt_link_paths "$links")
EOF

  if [ -n "$setup" ]; then
    local setup_targets
    read -r -a setup_targets <<< "$setup"
    if ! make -C "$path" "${setup_targets[@]}"; then
      _wt_step_failed "setup" "$repo" "$branch" "$path"
      return 1
    fi
  fi

  _wt_write_env "$repo" "$branch" "$slug" "$path" "$primary_root" "$project" "$ports" || return 1
  printf 'wt: created %s on branch %s\n' "$path" "$branch"
}

# _wt_write_env <repo> <branch> <slug> <path> <primary> <project> <ports> — resolve ports (when
# declared) and write <path>/.worktree.env atomically, mode 0600.
_wt_write_env() {
  local repo="$1" branch="$2" slug="$3" path="$4" primary="$5" project="$6" ports="$7"
  local tmp pair
  local -a service_args=()
  tmp="$(mktemp "$path/.worktree.env.XXXXXX")" || {
    _wt_step_failed "ports" "$repo" "$branch" "$path"
    return 1
  }
  if [ -n "$ports" ]; then
    set -f
    for pair in $(printf '%s' "$ports" | tr ',' ' '); do
      service_args+=(--service "$pair")
    done
    set +f
    if ! python3 "$SCRIPT_DIR/lib/wt_ports.py" resolve --project "$project" --slug "$slug" \
      "${service_args[@]}" --output "$tmp"; then
      rm -f "$tmp"
      _wt_step_failed "ports" "$repo" "$branch" "$path"
      return 1
    fi
  fi
  if ! {
    printf 'SLUG=%s\nPROJECT=%s\nCOMPOSE_PROJECT_NAME=%s\nPRIMARY_ROOT=%s\nIS_PRIMARY=0\n' \
      "$slug" "$project" "$project" "$primary" >> "$tmp" \
      && chmod 600 "$tmp" \
      && mv "$tmp" "$path/.worktree.env"
  }; then
    rm -f "$tmp"
    _wt_step_failed "env file" "$repo" "$branch" "$path"
    return 1
  fi
}

cmd_rm() {
  local repo="$1" branch="$2" primary_root path
  _wt_preamble worktree-rm "$repo" || return 1
  _wt_find_branch "$REPO_DIR" "$branch" || return 1
  if [ -z "$_WT_FOUND_PATH" ]; then
    wt_die "no worktree for $branch in $repo"
    return 1
  fi
  path="$_WT_FOUND_PATH"
  primary_root="$(wt_primary_root "$REPO_DIR")" || return 1
  if [ "$_WT_FOUND_PRIMARY" -eq 1 ]; then
    wt_die "$branch is checked out in the primary checkout $path; refusing to remove it"
    return 1
  fi
  case "$path" in
    "$primary_root"/.claude/worktrees/?*) ;;
    *)
      wt_die "worktree $path is outside $primary_root/.claude/worktrees/; refusing to remove it"
      return 1
      ;;
  esac
  if [ "$_WT_FOUND_LOCKED" -eq 1 ]; then
    wt_die "worktree $path is locked; run git worktree unlock first"
    return 1
  fi
  if [ "$OWNED" -eq 1 ]; then
    local owned_rc=0
    make -C "$path" worktree-rm || owned_rc=$?
    return "$owned_rc"
  fi
  _wt_rm_generic "$repo" "$path"
}

_wt_rm_generic() {
  local repo="$1" path="$2"
  local teardown ports project expected recorded
  local -a teardown_targets
  teardown="$(seam_raw "$REPO_DIR/CLAUDE.md" "Worktree teardown")"
  ports="$(seam_raw "$REPO_DIR/CLAUDE.md" "Worktree ports")"

  if [ ! -d "$path" ]; then
    wt_warn "$path is missing on disk; skipping teardown and docker cleanup"
  else
    project=""
    if [ -n "$ports" ] || [ -n "$teardown" ]; then
      # The compose project is derived from the worktree directory name (the slug `new` used),
      # never by re-sanitizing the branch. A recorded PROJECT that disagrees (stale or copied
      # env file) is refused before anything runs: `down -v --rmi local` on someone else's
      # project would destroy their volumes.
      expected="$(wt_project_name "$repo" "$(basename -- "$path")")" || return 1
      recorded=""
      if [ -f "$path/.worktree.env" ]; then
        recorded="$(grep -m1 '^PROJECT=' "$path/.worktree.env" || true)"
        recorded="${recorded#PROJECT=}"
      fi
      if [ -z "$recorded" ]; then
        project="$expected"
        wt_warn "no PROJECT in $path/.worktree.env; using $project"
      elif [ "$recorded" != "$expected" ]; then
        wt_die "PROJECT \"$recorded\" in $path/.worktree.env does not match the expected compose project \"$expected\"; refusing to tear anything down, worktree left in place"
        return 1
      else
        project="$recorded"
      fi
    fi
    if [ -n "$teardown" ]; then
      read -r -a teardown_targets <<< "$teardown"
      if ! make -C "$path" "${teardown_targets[@]}"; then
        wt_die "Worktree teardown (${teardown}) failed; worktree left in place at $path"
        return 1
      fi
    fi
    if [ -n "$ports" ] || [ -n "$teardown" ]; then
      if command -v docker > /dev/null 2>&1; then
        if ! (cd "$path" && docker compose --project-name "$project" down -v --remove-orphans --rmi local); then
          wt_die "docker compose down failed for project $project; worktree left in place at $path"
          return 1
        fi
      elif [ -n "$ports" ]; then
        wt_die "docker is not on PATH; cannot reclaim compose project $project, worktree left in place"
        return 1
      else
        wt_warn "docker is not on PATH; skipped compose cleanup for $project"
      fi
    fi
  fi

  # Non-force on purpose: git's refusal (dirty, untracked, locked) is surfaced verbatim.
  git -C "$REPO_DIR" worktree remove "$path" || return 1
  printf 'wt: removed %s\n' "$path"
}

# _wt_ls_repo <name> <dir> — one row per worktree git reports, primary included.
_wt_ls_repo() {
  local name="$1" dir="$2" out line cur="" br="" flags="" first=1 have=0
  out="$(git -C "$dir" worktree list --porcelain)" || return 1
  while IFS= read -r line; do
    case "$line" in
      "worktree "*)
        cur="${line#worktree }"
        br="(none)"
        flags=""
        have=1
        ;;
      "branch "*) br="${line#branch refs/heads/}" ;;
      detached) br="(detached)"; flags="$flags [detached]" ;;
      locked | "locked "*) flags="$flags [locked]" ;;
      prunable | "prunable "*) flags="$flags [prunable]" ;;
      "")
        if [ "$have" -eq 1 ]; then
          # The first block is always the primary checkout; wt_is_primary confirms it on disk
          # (a missing directory would otherwise resolve to the parent repo).
          if [ "$first" -eq 1 ] && [ -d "$cur" ] && wt_is_primary "$cur"; then
            flags="$flags [primary]"
          fi
          printf '%s  %s  %s%s\n' "$name" "$br" "$cur" "$flags"
          first=0
          have=0
        fi
        ;;
    esac
  done <<EOF
$out

EOF
}

cmd_ls() {
  local repo="${1:-}" dir
  if [ -n "$repo" ]; then
    _wt_repo_dir "$repo" || return 1
    _wt_ls_repo "$repo" "$REPO_DIR"
    return
  fi
  for dir in "$STRONGHOLD"/*/; do
    dir="${dir%/}"
    [ -e "$dir/.git" ] || continue
    _wt_ls_repo "$(basename -- "$dir")" "$dir" || wt_warn "could not list the worktrees of $dir"
  done
  return 0
}

main() {
  local arg cmd="" a1="" a2="" extra="" count=0
  for arg in "$@"; do
    case "$arg" in
      --init) INIT=1 ;;
      -h | --help)
        sed -n '2,/^# Exit:/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
        return 0
        ;;
      -*)
        printf 'wt.sh: unknown option %s\n' "$arg" >&2
        usage
        return 2
        ;;
      *)
        count=$((count + 1))
        case "$count" in
          1) cmd="$arg" ;;
          2) a1="$arg" ;;
          3) a2="$arg" ;;
          *) extra="$arg" ;;
        esac
        ;;
    esac
  done
  if [ -n "$extra" ]; then
    usage
    return 2
  fi
  case "$cmd" in
    new | rm)
      if [ "$count" -ne 3 ] || [ -z "$a1" ] || [ -z "$a2" ]; then
        usage
        return 2
      fi
      "cmd_$cmd" "$a1" "$a2"
      ;;
    ls)
      if [ "$INIT" -eq 1 ] || [ "$count" -gt 2 ]; then
        usage
        return 2
      fi
      cmd_ls "$a1"
      ;;
    *)
      usage
      return 2
      ;;
  esac
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
