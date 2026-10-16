#!/usr/bin/env bash
# PreToolUse(Bash) hook: pre-approve a few always-safe entrypoints so they don't
# trip a permission prompt — `date`, `rm` confined to /tmp/claude, and `mkdir -p`
# for plans/ or changelog/ dirs. (Extracted from app-main's inline settings hook
# so the escaping lives in a script, not in settings.json.)
set -eu

cmd=$(jq -r '.tool_input.command // empty')
[ -z "$cmd" ] && exit 0

first=$(printf '%s' "$cmd" | awk '{print $1}' | sed 's|.*/||')

allow() {
  jq -n --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"allow",permissionDecisionReason:$r}}'
  exit 0
}

case "$first" in
  date)
    allow "date commands are always safe"
    ;;
  rm)
    if printf '%s' "$cmd" | grep -qE '^rm[[:space:]]+(-[rf]+[[:space:]]+)?(/tmp/claude/|/private/tmp/claude/)' \
       && ! printf '%s' "$cmd" | grep -qE '\.\.|\.\*|[;|&]'; then
      allow "rm within /tmp/claude/ is safe for test cleanup"
    fi
    ;;
  mkdir)
    if printf '%s' "$cmd" | grep -qE '^mkdir[[:space:]]+-p[[:space:]]+(plans/|changelog/)'; then
      allow "mkdir for plans/changelog dirs is safe"
    fi
    ;;
esac

exit 0
