#!/usr/bin/env bash
# PreToolUse(Bash) hook: enforce the `Worktree policy` Claude Config key so a worktree can never
# duplicate a live bot or repoint production. Parses `make` invocations (python3 shlex helper) and
# denies, per the repo's policy: build-only (allow-list), no policy (deny-by-default patterns) and
# singleton-runtime (guarded targets must go through wt-singleton.sh).
#
# Fails OPEN (exit 0, no output) on any parse/sourcing error, missing jq/python3, or an unresolvable
# directory, like block-unsafe-push.sh. This is an anti-accident control, not a sandbox: see the
# live-resource-guard plan, "Known gaps".
#
# The `-C` resolution lives in lib/wt_parse_make.py. It intentionally differs from the git `-C`
# walk in block-unsafe-push.sh: it also handles --directory, attached forms (-C<dir>) and
# clustered flags, and never walks backwards from a `push` token.
set -u

input="$(cat)"
command=$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null)
[ -n "$command" ] || exit 0
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)
[ -n "$cwd" ] || cwd="${CLAUDE_PROJECT_DIR:-$PWD}"

# Fast path: a plain substring test (so `gmake` and /usr/bin/make are not missed); the precise
# match on the command word happens in wt_parse_make.py.
case "$command" in
  *make*) ;;
  *) exit 0 ;;
esac

# shellcheck source=../scripts/lib/worktree-guard.sh
. "$(dirname "${BASH_SOURCE[0]}")/../scripts/lib/worktree-guard.sh" 2>/dev/null || exit 0

wtg_parse_make "$command" "$cwd" || exit 0
top=$(repo_toplevel "$WTG_DIR") || exit 0
reason=$(wtg_decide "$top") || exit 0
[ -n "$reason" ] || exit 0

jq -n --arg r "$reason" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
exit 0
