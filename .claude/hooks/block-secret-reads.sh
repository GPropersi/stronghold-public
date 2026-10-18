#!/usr/bin/env bash
# PreToolUse hook (Read): DENY reading secret-bearing files (.env, .env.*,
# secrets/ dirs, config/credentials.json). Replaces the permissions.deny
# Read(**/...) globs: on Linux the sandbox ignores glob patterns in Read/Edit
# rules and `claude update` warns about them, so the rule is enforced here.

set -eu

file_path=$(jq -r '.tool_input.file_path // empty')
[ -z "$file_path" ] && exit 0

if echo "$file_path" | grep -qE '(^|/)(\.env(\..*)?|config/credentials\.json)$|(^|/)secrets/'; then
  jq -n --arg p "$file_path" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: "Refusing to read \($p) — secret-bearing files (.env, .env.*, secrets/, config/credentials.json) are off-limits. Ask the user for the specific non-secret value you need."
    }
  }'
  exit 0
fi

exit 0
