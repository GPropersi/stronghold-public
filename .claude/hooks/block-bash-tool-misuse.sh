#!/usr/bin/env bash
# PreToolUse(Bash) hook: deny DIRECT execution of ephemeral wrapper scripts out
# of /tmp/claude/ (e.g. `/tmp/claude/run.sh`). The sibling hook
# block-scratchpad-wrappers.sh covers the interpreter form (`bash /tmp/claude/run.sh`);
# this one covers the case where the script path is the first token.
#
# NOTE: this hook used to also map ls/find/cat/head/tail/grep/rg/sed/awk onto the
# dedicated Glob/Read/Grep/Edit tools. That mapping was removed — it emitted
# permissionDecision "block", which is not a valid value (only allow|deny|ask),
# so every ls/cat/grep Bash call printed a "Hook JSON output validation failed"
# error and the decision was discarded anyway. Bare grep/cat/sed in Bash is
# expected here (see CLAUDE.md "Sandbox & grep"), so the mapping is not restored.

set -eu

cmd=$(jq -r '.tool_input.command // empty')
[ -z "$cmd" ] && exit 0

first_full=$(echo "$cmd" | awk '{print $1}')

case "$first_full" in
  /tmp/claude/*.sh|/private/tmp/claude/*.sh)
    jq -n '{
      "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": "Do not run wrapper scripts from /tmp/claude/ — they are ephemeral and not allowlisted, so each invocation prompts. Use an existing make target (run `make help` or check the Makefile), or move the wrapper to .claude/scripts/<name>.sh and add a Bash(.claude/scripts/<name>.sh:*) allow rule. /tmp/claude/ is for transient OUTPUT files (test logs, intermediate JSON) only — never executable wrappers."
      }
    }'
    exit 0
    ;;
esac

exit 0
