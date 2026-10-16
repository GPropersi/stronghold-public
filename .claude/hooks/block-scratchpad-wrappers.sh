#!/usr/bin/env bash
# PreToolUse(Bash) hook: block running throwaway wrapper scripts out of scratch
# space (scratchpad/, /tmp/claude, $TMPDIR, /var/folders T dirs).
#
# Agents sometimes bundle `cd <dir> && <cmd> > /tmp/claude/out.txt` into a
# generated `.sh` in the scratchpad and run `bash <that-script>`. That wrapper
# invocation is never on any allowlist, so it fires a permission PROMPT on every
# run. Blocking it (with a corrective reason returned to the model — NOT a user
# prompt) forces the agent to run the underlying command directly, which the
# allow-tmp-access hook then pre-approves prompt-free.
#
# Returns {"decision":"block","reason":"..."} so the calling model self-corrects.

set -eu

cmd=$(jq -r '.tool_input.command // empty')
[ -z "$cmd" ] && exit 0

block() {
  jq -n --arg reason "$1" '{"decision":"block","reason":$reason}'
  exit 0
}

# Match: (bash|sh|zsh) <path> where <path> points into scratch space.
if printf '%s' "$cmd" | grep -qE '(^|[[:space:]|;&(])(bash|sh|zsh)[[:space:]]+[^[:space:]]*(scratchpad/|/tmp/claude/|/private/tmp/claude|/var/folders/[^[:space:]]*/T/|\$TMPDIR/)'; then
  block "Don't run wrapper scripts from scratch space (scratchpad/ or /tmp/claude) — 'bash <that-script>' is never allowlisted and prompts the user every time. Run the underlying command DIRECTLY instead: (1) change directory with a STANDALONE 'cd <dir>' Bash call (the working dir persists across calls — do not compound 'cd x && cmd'); (2) put the output redirect on the command itself, e.g. 'npm run build > /tmp/claude/web-build.txt 2>&1' or 'go test ./... > /tmp/claude/go.txt 2>&1'. Any command redirecting to /tmp/claude/ is auto-approved by the allow-tmp-access hook, so it runs with no prompt. Then Read that file for results."
fi

exit 0
