#!/usr/bin/env bash
# PreToolUse(Bash) hook: refuse a bot push that has no passing push-gate result for the pushed
# branch's tip sha. Gates two command shapes: the sanctioned `gh-app-push.sh [<branch>]` (every
# spelling, including app-notifs' local copy) and a raw `git push` in a repo that declares a
# `Bot push script` (this closes the `x-access-token:` bypass of block-unsafe-push.sh rule 4).
#
# All command parsing lives in lib/push_gate_cmd.py (fail-closed argument rules, the push-anchored
# `-C` walk, every command segment and `bash -c` string judged) and ALL result validation in
# `lib/push_gate.py check`; this file only wires them.
# The stronghold and repos with `Bot push script: n/a` (or no seam) are exempt.
#
# Fails OPEN (exit 0, no output) on a missing jq/python3, an unparsable payload or an unresolvable
# directory, like block-unsafe-push.sh: a guard must never block on its own error.
set -u

input="$(cat)"
command=$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null) || exit 0
[ -n "$command" ] || exit 0

# Fast path: only a gh-app-push.sh mention or a git ... push can matter.
case "$command" in
  *gh-app-push.sh*) ;;
  *git*push*) ;;
  *) exit 0 ;;
esac

cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)
[ -n "$cwd" ] || cwd="${CLAUDE_PROJECT_DIR:-$PWD}"

hook_dir="$(dirname "${BASH_SOURCE[0]}")"
lib_dir="$hook_dir/../scripts/lib"
# shellcheck source=../scripts/lib/repo-root.sh
. "$lib_dir/repo-root.sh" 2>/dev/null || exit 0
# shellcheck source=../scripts/lib/seam.sh
. "$lib_dir/seam.sh" 2>/dev/null || exit 0

parsed=$(python3 "$lib_dir/push_gate_cmd.py" "$cwd" "$command" 2>/dev/null) || exit 0

emit_deny() {
  jq -n --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
  exit 0
}

# One judgment per gated invocation (every segment of the command, `bash -c` strings included).
while IFS= read -r item; do
  [ -n "$item" ] || continue
  kind=$(printf '%s' "$item" | jq -r '.kind // "none"') || exit 0
  [ "$kind" != "none" ] || continue
  target=$(printf '%s' "$item" | jq -r '.dir // empty') || exit 0
  branch=$(printf '%s' "$item" | jq -r '.branch // empty') || exit 0
  deny=$(printf '%s' "$item" | jq -r '.deny // empty') || exit 0
  allow=$(printf '%s' "$item" | jq -r '.allow // false') || exit 0

  top=$(repo_toplevel "$target") || continue
  # The stronghold is judged on the resolved checkout, not the raw cwd (a subdirectory cwd).
  is_stronghold_root "$top" && continue

  if [ "$kind" = "gitpush" ]; then
    bot_script=$(seam_first_token "$top/CLAUDE.md" 'Bot push script')
    [ -n "$bot_script" ] || continue
  fi

  if [ -n "$deny" ]; then
    emit_deny "push-gate: $deny"
  fi
  [ "$allow" = "true" ] && continue
  [ -n "$branch" ] || continue

  sha=$(git -C "$top" rev-parse --verify --quiet "refs/heads/$branch" 2>/dev/null) || continue
  if reason=$(python3 "$lib_dir/push_gate.py" check -C "$top" "$branch" 2>/dev/null); then
    continue
  fi
  emit_deny "push-gate: refusing the bot push of $branch (tip $sha): ${reason:-gate check failed}. Run ~/code/.claude/scripts/push-gate.sh $branch from $top (its own Bash call) and push only after it passes."
done < <(printf '%s' "$parsed" | jq -c '.items[]?' 2>/dev/null)

exit 0
