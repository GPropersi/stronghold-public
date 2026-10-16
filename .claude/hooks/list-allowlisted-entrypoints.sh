#!/usr/bin/env bash
# SessionStart hook: front-load the model with a list of allowlisted entry points
# (Makefile targets and .claude/scripts/) so it doesn't invent new wrappers in
# /tmp/claude/ that need fresh permission each session.

set -eu

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-.}"

make_targets=""
if [ -f "$PROJECT_DIR/Makefile" ]; then
  make_targets=$(awk -F':.*?## ' '/^[a-zA-Z][a-zA-Z0-9_-]*:.*?##/ {printf "  make %-25s — %s\n", $1, $2}' "$PROJECT_DIR/Makefile" || true)
fi

# Additively surface the ACTIVE checkout's Makefile targets when this session is
# operating inside a sub-repo or one of its linked worktrees — resolved from the
# git toplevel of $PWD (so a worktree advertises its own Makefile, not the
# primary's). The root Makefile above is usually absent (the stronghold has none),
# so this is what actually front-loads make targets during real sub-repo work.
# Degrades to nothing when not inside a sub-repo, in the stronghold itself, or the
# checkout has no Makefile (or the helper can't be sourced).
# shellcheck source=../scripts/lib/repo-root.sh
. "$(dirname "${BASH_SOURCE[0]}")/../scripts/lib/repo-root.sh" 2>/dev/null || true

subrepo_block=""
top="$(repo_toplevel "$PWD" 2>/dev/null || true)"
# Only advertise checkouts under the stronghold ("auto-unsandboxed" is only true there):
# sub-repos and their <repo>/.claude/worktrees/<slug> worktrees both qualify.
top_real="$(CDPATH='' cd -- "${top:-/nonexistent}" 2>/dev/null && pwd -P || true)"
project_real="$(CDPATH='' cd -- "$PROJECT_DIR" 2>/dev/null && pwd -P || true)"
in_project=false
if [ -n "$project_real" ]; then
  case "$top_real" in
    "$project_real"/*) in_project=true ;;
  esac
fi
if [ "$in_project" = true ] && ! is_stronghold_root "$top" && [ -f "$top/Makefile" ]; then
  subrepo_targets=$(awk -F':.*?## ' '/^[a-zA-Z][a-zA-Z0-9_-]*:.*?##/ {printf "  make %-25s — %s\n", $1, $2}' "$top/Makefile" || true)
  if [ -n "$subrepo_targets" ]; then
    label_name="$(basename "$top")"
    label_suffix=""
    if repo_is_linked_worktree "$top"; then
      primary="$(repo_primary_root "$top" 2>/dev/null || true)"
      label_name="$(basename "${primary:-$top}")"
      label_suffix=" — worktree $top"
    fi
    subrepo_block="

Active sub-repo ($label_name)$label_suffix Makefile targets (run from within $top; auto-unsandboxed — never prompt):
$subrepo_targets"
  fi
fi

scripts_block="  (none)"
if [ -d "$PROJECT_DIR/.claude/scripts" ]; then
  found=$(find "$PROJECT_DIR/.claude/scripts" -maxdepth 1 -type f -name '*.sh' 2>/dev/null \
    | sed "s|$PROJECT_DIR/||" \
    | sort \
    | sed 's|^|  |')
  if [ -n "$found" ]; then
    scripts_block="$found"
  fi
fi

context="ALLOWLISTED BASH ENTRY POINTS — use these instead of composing new Bash commands or dropping wrappers in /tmp/claude/.

Make targets (auto-unsandboxed via sandbox.excludedCommands — never prompt):
${make_targets:-  (no Makefile found at $PROJECT_DIR/Makefile)}
${subrepo_block}

Project scripts in .claude/scripts/ (durable, allowlisted via Bash(...:*) rules in settings):
${scripts_block}

RULE: before composing any Bash command (tests, docker, git, build), check this list first. If your task is not covered, propose either:
  1. a new Makefile target (preferred — make is already excluded), or
  2. a durable script in .claude/scripts/ + an allow rule.

Do NOT drop wrapper scripts in /tmp/claude/ to dodge hooks — those paths are ephemeral, not allowlisted, blocked at the harness, and create a fresh prompt each session."

jq -n --arg ctx "$context" '{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": $ctx
  }
}'
