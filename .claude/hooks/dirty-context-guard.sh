#!/usr/bin/env bash
# UserPromptSubmit hook: stop a heavy orchestrator skill from starting on a large, stale context.
# Those skills keep all state in files (plans, reviews, commits), so a /clear loses nothing, but
# every orchestrator turn re-reads the whole context. First submission is blocked with a nudge;
# submitting the identical prompt again passes. Threshold: DIRTY_CTX_TOKENS (default 100000).

set -eu

input=$(cat)
prompt=$(printf '%s' "$input" | jq -r '.prompt // ""')
transcript=$(printf '%s' "$input" | jq -r '.transcript_path // ""')
session=$(printf '%s' "$input" | jq -r '.session_id // "x"')

skills='plan-reviewer|git-push|run-plan|run-review|plan-creator|master-plan-creator|master-plan-orchestrator|pr-review|pr-review-fixer|apply-plan-review|design-doc|split-pr'
if ! printf '%s' "$prompt" | grep -Eq "^/($skills)( |\$)"; then
  exit 0
fi
[ -n "$transcript" ] && [ -f "$transcript" ] || exit 0

tokens=$(tac "$transcript" | grep -m1 '"type":"assistant"' | jq -r '.message.usage | ((.input_tokens // 0) + (.cache_read_input_tokens // 0) + (.cache_creation_input_tokens // 0))' 2>/dev/null || echo 0)
tokens=${tokens:-0}
threshold=${DIRTY_CTX_TOKENS:-100000}
[ "$tokens" -gt "$threshold" ] || exit 0

marker_dir="${TMPDIR:-/tmp}/claude-dirty-ctx"
mkdir -p "$marker_dir"
marker="$marker_dir/$session-$(printf '%s' "$prompt" | cksum | cut -d' ' -f1)"
if [ -f "$marker" ]; then
  rm -f "$marker"
  exit 0
fi
: > "$marker"

reason="Context is about $((tokens / 1000))k tokens. This skill keeps all state in files, so run /clear first and re-send the command: every turn of the skill would otherwise re-read this whole context. To run it anyway, submit the identical prompt again."
jq -n --arg reason "$reason" '{decision: "block", reason: $reason}'
