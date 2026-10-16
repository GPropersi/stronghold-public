# shellcheck shell=bash
# worktree-guard.sh — shared, SOURCED decision logic for the live-resource guard
# (.claude/hooks/block-worktree-live-resources.sh). Never executed directly: no shebang path,
# no top-level set -e/exit/output. Bash-3.2-safe (macOS): no mapfile, no associative arrays.
#
# Consumers: the hook above (and, from a later plan step, wt-singleton.sh via wtg_policy_md).
#
# Policy source (primary wins): the PRIMARY clone's CLAUDE.md decides when it declares a
# `Worktree policy`; only when it declares none does the checkout's own CLAUDE.md decide.
# Fails open (return 1 / no output) on any read problem. See the live-resource-guard plan.

_WT_GUARD_LIB_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/repo-root.sh
. "$_WT_GUARD_LIB_DIR/repo-root.sh"
# shellcheck source=lib/seam.sh
. "$_WT_GUARD_LIB_DIR/seam.sh"

# Targets denied in a non-primary worktree when the repo declares no `Worktree policy`.
WT_GUARD_DENY_PATTERNS='up up-* *-up *-install deploy *deploy* *tunnel* vps-*'

# Absolute wrapper path derived from this lib's own location (logical pwd, not -P, so a
# ~/code path stays in that form). Stored raw: the denial quotes it with printf %q at print time.
WT_GUARD_WRAPPER="$(CDPATH='' cd -- "$_WT_GUARD_LIB_DIR/.." && pwd)/wt-singleton.sh"

# wtg_parse_make <command> <payload-cwd> — tokenise the make command with the python helper and
# set WTG_DIR (resolved directory) and WTG_TARGETS (newline-separated targets). Returns 1 when
# python3 is missing or the command has an unsupported shape (the caller fails open). Call it
# BARE (never inside $(...) or a pipeline) so the globals survive, and before wtg_decide.
wtg_parse_make() {
  local out
  command -v python3 >/dev/null 2>&1 || return 1
  out=$(python3 "$_WT_GUARD_LIB_DIR/wt_parse_make.py" "$2" "$1" 2>/dev/null) || return 1
  # consumed by the hook, which sources this file
  # shellcheck disable=SC2034
  WTG_DIR="${out%%$'\n'*}"
  # consumed by the hook, which sources this file
  # shellcheck disable=SC2034
  WTG_TARGETS="${out#*$'\n'}"
  return 0
}

# wtg_glob_match <target> <list> — 0 iff <target> matches a glob in the whitespace/comma
# separated <list>. No pathname expansion happens (set -f while the list is word-split).
wtg_glob_match() {
  local target="${1:-}" list glob result=1 had_noglob=0
  list=$(printf '%s' "${2:-}" | tr ',`' '  ')
  case $- in *f*) had_noglob=1 ;; esac
  set -f
  for glob in $list; do
    # shellcheck disable=SC2254
    case "$target" in $glob) result=0; break ;; esac
  done
  if [ "$had_noglob" -eq 0 ]; then
    set +f
  fi
  return "$result"
}

# wtg_policy_md <checkout-toplevel> — print the path of the CLAUDE.md that decides (primary wins).
wtg_policy_md() {
  local top="${1:-}" primary
  primary=$(repo_primary_root "$top") || return 1
  if [ -n "$(seam_raw "$primary/CLAUDE.md" 'Worktree policy')" ]; then
    printf '%s\n' "$primary/CLAUDE.md"
  else
    printf '%s\n' "$top/CLAUDE.md"
  fi
}

# wtg_decide <checkout-toplevel> — print nothing (allow) or one reason line (deny); returns 0
# either way, 1 on a read problem (fail open). Reads the global WTG_TARGETS set by wtg_parse_make.
wtg_decide() {
  local top="${1:-}" md policy allowed guarded repo target cmd shown
  [ -n "${WTG_TARGETS:-}" ] || return 0
  is_stronghold_root "$top" && return 0
  md=$(wtg_policy_md "$top") || return 1
  [ -f "$md" ] || return 0
  # Gate (as block-unsafe-push.sh): only a CLAUDE.md with a `## Claude Config` section counts.
  grep -q '^## Claude Config' "$md" 2>/dev/null || return 0
  policy=$(seam_raw "$md" 'Worktree policy')
  allowed=$(seam_raw "$md" 'Worktree allowed targets')
  guarded=$(seam_raw "$md" 'Worktree guarded targets')
  repo=$(basename "$(repo_primary_root "$top")")

  # singleton-runtime applies in every checkout; every other rule only in a linked worktree.
  if [ "$policy" != "singleton-runtime" ]; then
    repo_is_linked_worktree "$top" || return 0
  fi

  while IFS= read -r target; do
    [ -n "$target" ] || continue
    case "$policy" in
      singleton-runtime)
        if wtg_glob_match "$target" "$guarded"; then
          cmd=$(printf '%q run %q %q' "$WT_GUARD_WRAPPER" "$top" "$target")
          printf '%s\n' "make $target is a guarded singleton target of $repo (Worktree policy: singleton-runtime): only one live instance may run. Start it through the locking wrapper instead: $cmd"
          return 0
        fi
        ;;
      build-only)
        if [ "$target" != "help" ] && ! wtg_glob_match "$target" "$allowed"; then
          shown="${allowed:-<empty: help only>}"
          printf '%s\n' "make $target is not allowed in a worktree of $repo (Worktree policy: build-only). Worktree allowed targets: $shown. Run it from the primary clone, or add it to \`Worktree allowed targets\` in the primary clone's CLAUDE.md (change it on main: a worktree's own edit is ignored once the primary declares a policy)."
          return 0
        fi
        ;;
      full) ;;
      '')
        if wtg_glob_match "$target" "$WT_GUARD_DENY_PATTERNS"; then
          printf '%s\n' "make $target is denied in a worktree of $repo: no \`Worktree policy\` is declared, and this target can start or repoint a live resource. Declare \`Worktree policy: full\` (or build-only / singleton-runtime) in the repo's ## Claude Config (on main, or in the first commit of the adoption branch, which is honored while the primary declares none), or run it from the primary clone."
          return 0
        fi
        ;;
      *) ;; # invalid policy value: fail open (wt.sh validates policy values)
    esac
  done <<< "$WTG_TARGETS"
  return 0
}
