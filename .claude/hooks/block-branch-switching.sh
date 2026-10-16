#!/usr/bin/env bash
# PreToolUse hook (matchers: Bash and EnterWorktree): enforce at tool-call time the rule that a
# branch is never created or switched to by hand in a sub-repo. The sanctioned path is
# `make wt-new REPO=<repo> BRANCH=<b>` (which this hook does not see: scripts run git internally,
# only the agent's own Bash command is judged).
#
#   EnterWorktree    denied unconditionally (it has no tool_input.command).
#   Bash             lib/branch_guard.py classifies every git invocation in the command; this file
#                    applies the checkout-dependent rules to each flagged one:
#                      deny          refused in a sub-repo
#                      default-ok    `git checkout|switch <name>` allowed iff <name> is the default
#                                    branch (seam `Default branch`, else main)
#                      create-ahead  `git checkout -b` allowed only on the default branch when it
#                                    is ahead of origin/<default> (the confirm-gated /git-push
#                                    exception); an unresolvable count is 0, so it is denied
#                    The stronghold (judged on the resolved toplevel, so a subdirectory counts)
#                    works directly on main and is exempt.
#
# Fails OPEN (exit 0, no output) on a missing jq/python3, an unparsable payload, an absent or
# unknown tool_name, or an unresolvable directory: a guard must never block on its own error.
# NOT registered in settings.json until Step 9b of the deterministic-workflow-checks plan.
set -u

input="$(cat)"
tool=$(printf '%s' "$input" | jq -r '.tool_name // empty' 2>/dev/null) || exit 0

emit_deny() {
  jq -n --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
  exit 0
}

case "$tool" in
  EnterWorktree)
    emit_deny "branch-switching: EnterWorktree is not allowed (its relocation prompt cannot be auto-approved). Create the worktree with \`make wt-new REPO=<repo> BRANCH=<b>\` from ~/code (its own Bash call), then work in it with a standalone \`cd <repo>/.claude/worktrees/<slug>\`."
    ;;
  Bash) ;;
  *) exit 0 ;;
esac

command=$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null) || exit 0
[ -n "$command" ] || exit 0

# Fast path: only a git checkout / switch / branch / worktree mention can matter.
case "$command" in
  *git*) ;;
  *) exit 0 ;;
esac
case "$command" in
  *checkout*|*switch*|*branch*|*worktree*) ;;
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

parsed=$(python3 "$lib_dir/branch_guard.py" "$cwd" "$command" 2>/dev/null) || exit 0

while IFS= read -r item; do
  [ -n "$item" ] || continue
  target=$(printf '%s' "$item" | jq -r '.dir // empty') || exit 0
  form=$(printf '%s' "$item" | jq -r '.form // empty') || exit 0
  name=$(printf '%s' "$item" | jq -r '.name // empty') || exit 0
  hint=$(printf '%s' "$item" | jq -r '.hint // empty') || exit 0

  top=$(repo_toplevel "$target") || continue
  # The stronghold is judged on the resolved checkout, not the raw cwd (a subdirectory cwd).
  is_stronghold_root "$top" && continue

  default_branch=$(seam_first_token "$top/CLAUDE.md" 'Default branch' 2>/dev/null)
  [ -n "$default_branch" ] || default_branch="main"

  case "$form" in
    default-ok)
      [ "$name" = "$default_branch" ] && continue ;;
    create-ahead)
      current=$(git -C "$top" branch --show-current 2>/dev/null)
      ahead=$(git -C "$top" rev-list --count "origin/$default_branch..HEAD" 2>/dev/null)
      case "$ahead" in
        ''|*[!0-9]*) ahead=0 ;;
      esac
      if [ "$current" = "$default_branch" ] && [ "$ahead" -gt 0 ]; then
        continue
      fi ;;
  esac

  primary=$(repo_primary_root "$top" 2>/dev/null)
  repo=$(basename "${primary:-$top}")
  branch="${name:-<b>}"
  emit_deny "branch-switching: \`$hint\` is blocked in $repo. Create or switch to a branch with \`make wt-new REPO=$repo BRANCH=$branch\` from ~/code (its own Bash call) and work in that worktree. To restore files use \`git checkout -- <path>\`. Switching to the default branch ($default_branch) is allowed."
done < <(printf '%s' "$parsed" | jq -c '.items[]?' 2>/dev/null)

exit 0
