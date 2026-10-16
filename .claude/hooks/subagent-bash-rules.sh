#!/usr/bin/env bash
# SubagentStart hook: give every new subagent the Bash rules the PreToolUse guards enforce, once,
# at start. Each violation otherwise costs a wasted full-context turn (about 6% of total spend was
# hook denials, mostly compound commands from subagents, which never see SessionStart context).

set -eu

context="BASH RULES (enforced by hooks; every violation wastes a turn): ONE simple command per Bash call. No &&, ||, ;, heredocs, backticks, \$(...) or brace expansion (pipes | are fine). Run independent commands as parallel Bash calls in one message. Your Bash cwd resets between calls, so never cd: use git -C, make -C or absolute paths. Create files with the Write tool, never cat > or echo >. Prefer the helper scripts in ~/code/.claude/scripts: run-suite.sh <dir> <make-target> (tests/lint/build, compact result), git-snapshot.sh [dir] (repo state), plan-status.sh <repo>/<plan> --step N (one plan step), changelog-add.sh (changelog), wait-for.sh (waiting; never echo-poll)."

jq -n --arg ctx "$context" '{
  "hookSpecificOutput": {
    "hookEventName": "SubagentStart",
    "additionalContext": $ctx
  }
}'
