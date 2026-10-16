#!/usr/bin/env bash
# PreToolUse(Bash) hook: pre-approve commands that only touch throwaway scratch
# space — $TMPDIR, /tmp/claude, /var/folders T dirs, or plans/<topic>/tmp/.
# (Extracted from app-main's inline settings.local hook.)
set -eu

cmd=$(jq -r '.tool_input.command // empty')
[ -z "$cmd" ] && exit 0

if printf '%s' "$cmd" | grep -qE '\$TMPDIR/|/var/folders/.*/T/|/tmp/claude/|/private/tmp/claude|(^|[[:space:]]|/)plans/[^/]+/tmp(/|$|[[:space:]])'; then
  jq -n '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"allow",permissionDecisionReason:"TMPDIR / plans/*/tmp access is pre-approved"}}'
fi

exit 0
