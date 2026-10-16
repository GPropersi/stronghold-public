#!/usr/bin/env bash
# PreToolUse(Bash) guard: repo-parameterized push / gh safety.
#
# Re-adds app-main's four deferred push guards CENTRALLY, but derives every
# repo-specific value at runtime from the working sub-repo's `## Claude Config`
# seam + its `origin` remote — so it protects every repo that opts in WITHOUT
# hardcoding one repo, and WITHOUT breaking repos that push normally.
#
# Cross-cutting problem #3 in .claude/TIER-B-ROADMAP.md.
#
# ---------------------------------------------------------------------------
# WIRED INTO settings.json (Phase 11, 2026-07-12) as the first PreToolUse(Bash)
# guard. Activated after the app-main full-cycle smoke test validated the
# central push/PR flow. The logic is fail-open and conditional: repos that
# declare no `Bot push script` (personal repos, the stronghold itself) still
# push to their default branch freely; only force-push is blocked universally.
# ---------------------------------------------------------------------------
#
# Design — everything is CONDITIONAL and FAIL-OPEN:
#   * Force-push (--force / -f / --force-with-lease): blocked UNIVERSALLY (never
#     safe on a shared branch, regardless of repo).
#   * Direct push to the repo's Default branch: blocked ONLY when the repo
#     declares a `Bot push script` in its `## Claude Config` (i.e. it uses an
#     App-based PR workflow where direct default-branch pushes are disallowed).
#     Repos with no Bot-push-script declaration (personal repos, the stronghold)
#     push to their default branch freely.
#   * Bare / SSH `git push` when the repo declares a `Bot push script`: blocked
#     with guidance to push via that script (attributes to the bot, not the
#     user's personal account). The allowed remote is derived from `origin`.
#   * `gh` subcommands when the repo declares a `Token generator`: require a
#     `GH_TOKEN=$(...)` prefix (so gh runs as the App, not personal auth).
#
#   * Target repo: `git -C <dir> push` is judged against <dir> (stacked/relative -C
#     compose), and the `## Claude Config` is read only from that checkout's own
#     toplevel CLAUDE.md — never from a parent directory.
#   * Force-push and the gh GH_TOKEN-prefix pattern depend only on the command text,
#     so they are evaluated before the repo-root helper is sourced: a sourcing failure
#     can only skip the repo-dependent rules, never the force-push block.
#
# Any parse failure, missing config, or unknown shape => exit 0 (allow). A guard
# must never block on its own error.

set -u

input="$(cat)"
cmd="$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null)" || exit 0
[ -z "$cmd" ] && exit 0

# Only relevant for git-push and gh commands; everything else is allowed fast.
# The push regex tolerates global flags that take a value before `push`: -c <v>, -C <v>
# and --git-dir/--work-tree (space-separated or `=`). The leading anchor keeps a word that
# merely ends in "git" (e.g. "legit push") from matching.
is_git_push=false
is_gh=false
printf '%s' "$cmd" | grep -qE '(^|[;&|[:space:]])git([[:space:]]+(-[cC][[:space:]]+[^[:space:]]+|--(git-dir|work-tree)([[:space:]]+[^[:space:]]+|=[^[:space:]]+)))*[[:space:]]+push\b' && is_git_push=true
printf '%s' "$cmd" | grep -qE '(^|[;&|[:space:]])gh[[:space:]]+(pr|run|api|auth|issue|repo|release|project|secret|variable)\b' && is_gh=true
if [ "$is_git_push" = false ] && [ "$is_gh" = false ]; then
  exit 0
fi

block() {
  # $1 = reason string
  printf '{"decision":"block","reason":%s}\n' "$(printf '%s' "$1" | jq -R -s .)"
  exit 0
}

# --- Failure isolation: checks that need nothing but $cmd -------------------
# These run BEFORE the helper is sourced or any CLAUDE.md / toplevel lookup, so a
# sourcing failure below (which fails open with `exit 0`) can only skip the
# repo-dependent rules, never these.
#
# Force push: blocked universally (every repo, including the stronghold).
if [ "$is_git_push" = true ] \
   && printf '%s' "$cmd" | grep -qE '(^|[[:space:]])(--force|-f|--force-with-lease)([[:space:]]|=|$)'; then
  block "Force push is not allowed on any branch."
fi

# Does the command carry a GH_TOKEN=$(...) prefix? (Pattern match only; whether the rule
# applies at all depends on the repo's seam and is decided in the gh guards below.)
gh_has_token_prefix=false
if printf '%s' "$cmd" | grep -qE '^([A-Z_][A-Z0-9_]*=\S+[[:space:]]+)*GH_TOKEN=\$\(.+\)([[:space:]]+[A-Z_][A-Z0-9_]*=\S+)*[[:space:]]+gh\b'; then
  gh_has_token_prefix=true
fi

# --- Resolve the repo the command targets -----------------------------------
# Prefer the hook payload's cwd; fall back to $CLAUDE_PROJECT_DIR then $PWD.
cwd="$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)"
[ -z "$cwd" ] && cwd="${CLAUDE_PROJECT_DIR:-$PWD}"

# `git -C <dir> push` targets <dir>, not the payload cwd. Resolve it with a small token
# walk anchored on a `push` token (so `git log | git -C <dir> push` uses the invocation
# that feeds `push`, not the first `git`). Stacked -C compose like real git: an absolute
# <dir> replaces, a relative <dir> is joined onto the previous one. If no candidate walks
# cleanly up to `push`, cwd is left as-is. (`gh` has no -C, so this is push-only.)
if [ "$is_git_push" = true ]; then
  toks=()
  # Treat ; & | as token separators too, so `echo x|git -C <dir> push` still yields a bare `git`.
  tok_src="${cmd//[;&|]/ }"
  read -r -d '' -a toks <<< "$tok_src" || true
  resolved=""
  pi=$((${#toks[@]} - 1))
  while [ "$pi" -ge 1 ] && [ -z "$resolved" ]; do
    if [ "${toks[$pi]}" = "push" ]; then
      gi=$((pi - 1))
      while [ "$gi" -ge 0 ] && [ -z "$resolved" ]; do
        prev=""
        [ "$gi" -gt 0 ] && prev="${toks[$((gi - 1))]}"
        # A git invocation is a bare `git` or a path ending in /git, and is not itself
        # the value of a preceding flag (e.g. `-C /some/dir/git`).
        is_git_tok=false
        [ "${toks[$gi]##*/}" = "git" ] && is_git_tok=true
        case "$prev" in
          -C|-c|--git-dir|--work-tree) is_git_tok=false ;;
        esac
        if [ "$is_git_tok" = true ]; then
          walk_cwd="$cwd"
          i=$((gi + 1))
          while [ "$i" -lt "$pi" ]; do
            case "${toks[$i]}" in
              -C)
                d="${toks[$((i + 1))]:-}"
                d="${d#[\"\']}"
                d="${d%[\"\']}"
                [ -n "$d" ] || break
                # The shell has not expanded the command yet: expand a leading ~ or $HOME.
                case "$d" in
                  \~|\~/*) d="${HOME:-}${d#\~}" ;;
                  '$HOME'|'$HOME/'*) d="${HOME:-}${d#\$HOME}" ;;
                  '${HOME}'|'${HOME}/'*) d="${HOME:-}${d#\$\{HOME\}}" ;;
                esac
                [ -n "$d" ] || break
                case "$d" in
                  /*) walk_cwd="$d" ;;
                  *) walk_cwd="$walk_cwd/$d" ;;
                esac
                i=$((i + 2)) ;;
              -c|--git-dir|--work-tree)
                i=$((i + 2)) ;;                       # value is the next token
              --git-dir=*|--work-tree=*)
                i=$((i + 1)) ;;                       # value is inline
              *)
                break ;;                              # unrecognized token: not a clean walk
            esac
          done
          [ "$i" -eq "$pi" ] && resolved="$walk_cwd"
        fi
        gi=$((gi - 1))
      done
    fi
    pi=$((pi - 1))
  done
  [ -n "$resolved" ] && cwd="$resolved"
fi

# From here on the repo-root helper is needed (fail open if it can't be sourced).
# shellcheck source=../scripts/lib/repo-root.sh
. "$(dirname "${BASH_SOURCE[0]}")/../scripts/lib/repo-root.sh" 2>/dev/null || exit 0

# Read only the target checkout's OWN CLAUDE.md `## Claude Config`; never walk above the
# git toplevel. A repo with no CLAUDE.md (e.g. app-bot) would otherwise climb into the
# stronghold's schema template and inherit its placeholder Bot push script.
claude_md=""
top="$(repo_toplevel "$cwd" 2>/dev/null || true)"
if [ -n "$top" ] && [ -f "$top/CLAUDE.md" ] && grep -q '^## Claude Config' "$top/CLAUDE.md" 2>/dev/null; then
  claude_md="$top/CLAUDE.md"
fi

# Read a `- **Key:**` value out of the `## Claude Config` section (first match).
seam_value() {
  # $1 = key label (e.g. "Default branch")
  [ -z "$claude_md" ] && return 0
  sed -n '/^## Claude Config/,/^## /p' "$claude_md" \
    | grep -E "^- \*\*$1:\*\*" \
    | head -1 \
    | sed -E "s/^- \*\*$1:\*\*[[:space:]]*//"
}

default_branch="$(seam_value 'Default branch' | grep -oE '[A-Za-z0-9._/-]+' | head -1)"
[ -z "$default_branch" ] && default_branch="main"
bot_push_script="$(seam_value 'Bot push script')"
token_generator="$(seam_value 'Token generator')"

# A repo "opts in" to App-based push policy iff it declares a Bot push script
# that is not marked n/a.
app_push_repo=false
if [ -n "$bot_push_script" ] && ! printf '%s' "$bot_push_script" | grep -qiE '(^|[[:space:]])n/a([[:space:]]|$)'; then
  app_push_repo=true
fi
app_token_repo=false
if [ -n "$token_generator" ] && ! printf '%s' "$token_generator" | grep -qiE '(^|[[:space:]])n/a([[:space:]]|$)'; then
  app_token_repo=true
fi

# --- Identify the stronghold (base of operations) ---------------------------
# The stronghold (this ~/code repo, origin OWNER/stronghold) is the ONLY repo
# allowed to push directly to its default branch: it tracks workflow/skills/
# plans (not app source) and has no review gate. Every OTHER repo — App-based
# or not — is blocked from direct default-branch pushes below.
#
# Detect via the `origin` remote, NOT the `## Claude Config` seam: the stronghold's
# own toplevel CLAUDE.md is the schema TEMPLATE (which carries a placeholder `Bot
# push script`), and that must never be mistaken for a real bot-push repo —
# otherwise the stronghold's own direct-to-main pushes get falsely blocked. The
# CLAUDE.md lookup above never leaves the git toplevel, so only the stronghold
# itself ever sees that template, and `is_stronghold` still short-circuits it.
origin_url="$(git -C "$cwd" remote get-url origin 2>/dev/null || true)"
is_stronghold=false
if printf '%s' "$origin_url" | grep -qiE '[:/]OWNER/stronghold(\.git)?/?$'; then
  is_stronghold=true
fi

# --- git push guards --------------------------------------------------------
if [ "$is_git_push" = true ]; then
  # 1. Force push is already blocked universally, above (before the helper is sourced).

  # 2. The stronghold may push freely (non-force), including directly to main.
  if [ "$is_stronghold" = true ]; then
    exit 0
  fi

  # 3. Every NON-stronghold repo: a direct push to its default branch is blocked,
  #    whether or not it declares a Bot push script (this closes the gap for
  #    repos that declare `Bot push script: n/a`). Use a feature branch + PR.
  if printf '%s' "$cmd" | grep -qE "([[:space:]]|/|:)$default_branch([[:space:]]|$)"; then
    block "Only the stronghold may push directly to a default branch. In this repo, push to a feature branch and open a PR (or use its Bot push script)."
  fi

  # 4. App-push repos: a bare/SSH push must go through the declared Bot push
  #    script (or the App HTTPS x-access-token form) so it is attributed to the bot.
  if [ "$app_push_repo" = true ]; then
    if ! printf '%s' "$cmd" | grep -qE 'x-access-token:' \
       && ! printf '%s' "$cmd" | grep -qF "$bot_push_script"; then
      block "Bare git push attributes to your personal account. Push via the repo's Bot push script ($bot_push_script), which uses the GitHub App HTTPS token against this repo's origin remote."
    fi
  fi
  exit 0
fi

# --- gh guards --------------------------------------------------------------
if [ "$is_gh" = true ] && [ "$app_token_repo" = true ] && [ "$is_stronghold" = false ]; then
  # Require a GH_TOKEN=$(...) prefix so gh runs as the App, not personal auth.
  if [ "$gh_has_token_prefix" = false ]; then
    block "gh commands in this repo must be prefixed with GH_TOKEN=\$($token_generator) so they run as the GitHub App rather than your personal account."
  fi
fi

exit 0
