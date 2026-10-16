#!/usr/bin/env bash
# PreToolUse hook: auto-allow safe git subcommands regardless of leading global flags.
#
# Subagents frequently pin the target repo with global flags BEFORE the subcommand:
#   git -C /abs/path commit -m "..."
#   git --git-dir=/p/.git --work-tree=/p status --short
#   git -c user.name=... -c user.email=... commit -F /tmp/claude/msg.txt
# String allow-rules (`Bash(git -C * commit:*)`) can't match a variable path wedged
# between `git` and the subcommand. This hook parses the command instead: it strips the
# leading global flags, finds the REAL subcommand, and allows it if it's in the safe set
# — the same set already trusted for the bare `git <cmd>` form.
#
# Allow-only: emits an "allow" decision for safe git ops, else exits silently to let the
# default permission system and the other guard hooks decide. Because a "deny" from any
# PreToolUse hook wins over an "allow", the block-* guards (unsafe-push, compound, writes,
# command-substitution, …) still gate these commands — this hook cannot override them.

set -eu

cmd=$(jq -r '.tool_input.command // empty')
[ -z "$cmd" ] && exit 0

allow() {
  jq -n --arg r "$1" '{
    "hookSpecificOutput": {
      "hookEventName": "PreToolUse",
      "permissionDecision": "allow",
      "permissionDecisionReason": $r
    }
  }'
  exit 0
}

# Defer on anything that chains or substitutes another command — let the compound /
# substitution guards and the default system handle those. (2>&1 / > redirects are fine;
# their write targets are still policed by block-bash-writes.)
case "$cmd" in
  *'&&'*|*'||'*|*';'*|*'|'*|*'$('*|*'`'*) exit 0 ;;
esac

# Tokenize on whitespace. Quoted arguments (commit messages) split into several tokens,
# but they always come AFTER the subcommand, so they never affect subcommand detection.
read -ra toks <<< "$cmd"
[ "${#toks[@]}" -eq 0 ] && exit 0

# First token must be git (bare or an absolute path ending in /git).
base="${toks[0]##*/}"
[ "$base" = "git" ] || exit 0

# Walk past leading global flags to locate the real subcommand.
i=1
n=${#toks[@]}
while [ "$i" -lt "$n" ]; do
  t="${toks[$i]}"
  case "$t" in
    -C|-c)
      i=$((i + 2)); continue ;;                         # takes a separate argument
    --git-dir|--work-tree|--namespace|--exec-path|--super-prefix)
      i=$((i + 2)); continue ;;                         # bare form: value is next token
    --git-dir=*|--work-tree=*|--namespace=*|--exec-path=*|--super-prefix=*)
      i=$((i + 1)); continue ;;                         # attached form: value is inline
    -p|--paginate|-P|--no-pager|--bare|--no-replace-objects|--literal-pathspecs|--no-literal-pathspecs|--no-optional-locks|--icase-pathspecs)
      i=$((i + 1)); continue ;;                         # valueless global flags
    --)
      i=$((i + 1)); break ;;
    -*)
      exit 0 ;;                                          # unknown leading flag — defer, don't guess
    *)
      break ;;                                           # this is the subcommand
  esac
done

[ "$i" -lt "$n" ] || exit 0
sub="${toks[$i]}"

# --- Destructive-variant carve-out ------------------------------------------
# Several subcommands in the safe set below have data-losing variants (discarding
# working-tree changes, force-deleting a branch, dropping stashes, force-removing
# a worktree). Auto-allowing by subcommand alone would wave those through
# silently. Detect the destructive forms and DEFER (exit 0) so they fall through
# to a confirmation prompt instead. Index-walk (not array expansion) keeps this
# safe under macOS bash 3.2 + `set -u`.
destructive=false
j=$((i + 1))
while [ "$j" -lt "$n" ]; do
  case "$sub:${toks[$j]}" in
    checkout:-f|checkout:--force|checkout:--|checkout:.|checkout:-p|checkout:--patch) destructive=true; break ;;
    branch:-D|branch:-f|branch:--force) destructive=true; break ;;
    stash:drop|stash:clear) destructive=true; break ;;
    worktree:-f|worktree:--force) destructive=true; break ;;
  esac
  j=$((j + 1))
done
[ "$destructive" = true ] && exit 0

# Safe set == the git subcommands already allowed for the bare `git <cmd>` form in
# settings.json. Destructive ops (reset, clean, rebase, merge, cherry-pick) are NOT here.
case "$sub" in
  status|log|diff|show|branch|remote|fetch|pull|add|commit|push|checkout|stash|cherry|worktree)
    allow "Safe git subcommand '$sub' (leading global flags stripped) — matches the trusted git allow set" ;;
  *)
    exit 0 ;;
esac
