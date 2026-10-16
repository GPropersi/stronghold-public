#!/usr/bin/env bash
# PostToolUse(Edit|Write on **/*.js) hook: after a JS edit, run the affected
# sub-repo's declared Build command so import/export/syntax errors surface
# immediately — but ONLY for repos that declare a build. The build is resolved
# generically (no per-repo hardcoding) from the edited file's sub-repo: its
# CLAUDE.md `## Claude Config` Commands table supplies the `| Build | <cmd> |`
# row. Repos with no CLAUDE.md, no `## Claude Config`, or no Build row simply
# no-op, so editing JS anywhere in the stronghold never triggers a spurious
# build (preserving today's graceful-degradation behavior).
#
# Checkout-aware dispatch: the edited file's own git toplevel (a linked worktree
# at <repo>/.claude/worktrees/<slug>, or the primary clone) supplies the CLAUDE.md
# and is where the build runs. The stronghold itself is skipped: its CLAUDE.md holds
# a fenced seam template whose literal `<cmd>` Build row must never be executed.
#
# Always exits 0 — a PostToolUse hook must never block the edit that triggered it.
set -eu

# shellcheck source=../scripts/lib/repo-root.sh
. "$(dirname "${BASH_SOURCE[0]}")/../scripts/lib/repo-root.sh" 2>/dev/null || exit 0

file_path=$(jq -r '.tool_input.file_path // empty')
[ -z "$file_path" ] && exit 0

top="$(repo_toplevel "$file_path")" || exit 0
[ -n "$top" ] || exit 0
is_stronghold_root "$top" && exit 0

claude_md="$top/CLAUDE.md"
[ -f "$claude_md" ] || exit 0

# Resolve the Build command from the checkout's `## Claude Config` seam:
#   1. isolate the `## Claude Config` section (up to the next `## ` heading),
#   2. find the `| Build | <cmd> |` Commands-table row,
#   3. extract the second (command) column, trimming surrounding whitespace,
#   4. unwrap the cell when it is exactly one backticked command (`make x`); a cell
#      with trailing prose is left as-is and so stays a harmless no-op.
build_cmd=$(sed -n '/^## Claude Config/,/^## /p' "$claude_md" \
  | grep -E '^[[:space:]]*\|[[:space:]]*Build[[:space:]]*\|' \
  | head -1 \
  | sed -E 's/^[^|]*\|[^|]*\|[[:space:]]*([^|]*[^|[:space:]])[[:space:]]*\|.*/\1/')
build_cmd=$(printf '%s\n' "$build_cmd" | sed -E 's/^`([^`]*)`$/\1/')

# No build declared for this repo — nothing to do.
[ -z "$build_cmd" ] && exit 0

# Run the resolved build best-effort from the checkout root — same non-fatal
# style as the original hook; never fail the edit that triggered it.
cd "$top" 2>/dev/null || exit 0
$build_cmd 2>&1 | tail -5 || true

exit 0
