#!/usr/bin/env bash
#
# workflow-lint.sh — the checkable CLAUDE.md workflow rules as script checks (read-only, no network).
#
# Usage:
#   workflow-lint.sh [--repo <dir>] [--group seam|worktree|plans|hygiene|hooks|skills|all] [--json] [--staged]
#
# Output: severity<TAB>check-id<TAB>location<TAB>message lines (or a --json array of
# {severity,id,location,message}). Severities: error (exit 1), warn (exit 0), info.
# --repo <dir> scopes the seam/worktree/plans groups to one sub-repo directory name (the
# stronghold-wide groups then run only when named with --group). --staged limits the hygiene
# group to staged paths and scans the staged diff for token patterns.
# Required seam keys, staging overrides and allowlists live in .claude/seam-keys.json.
#
# Env: STRONGHOLD_DIR  override the stronghold root (test seam; default: this script's checkout,
#                      never the cwd)
#      HOME            used to expand `~` in seam paths (tests point it at a fixture)
# Exit: 0 no errors | 1 at least one error | 2 usage
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STRONGHOLD="${STRONGHOLD_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"

for arg in "$@"; do
  case "$arg" in
    -h|--help)
      sed -n '2,/^set /{/^set /!p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
  esac
done

exec python3 "$SCRIPT_DIR/lib/wf_lint.py" --root "$STRONGHOLD" --home "${HOME:-/}" "$@"
