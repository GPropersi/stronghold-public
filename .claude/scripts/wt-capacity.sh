#!/usr/bin/env bash
#
# wt-capacity.sh — read-only, machine-wide worktree capacity report for orchestrating skills.
#
# Usage:
#   wt-capacity.sh [<repo-dir>] [--json] [--meminfo PATH] [--cgroup PATH] [--capacity-file PATH]
#
# Prints, per repo, its `Worktree policy` and cost model plus live usable host memory and a single
# `agents_max` (and `suites_ceiling`/`spokes_ceiling`). Cost numbers are READ from app-main's
# docker/.capacity.generated.env; this script never writes and never reads Docker. It differs from
# app-main's `make capacity`, which GENERATES that per-clone file. Repos are enumerated like
# `wt.sh ls` (every stronghold dir with a .git and a CLAUDE.md); policies come from the seam.
#
# Env: STRONGHOLD_DIR  override the stronghold root (test seam; default: this script's checkout)
# Exit: 0 ok (degraded modes print a `note:` line) | 2 usage
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STRONGHOLD="${STRONGHOLD_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
# shellcheck source=lib/seam.sh
. "$SCRIPT_DIR/lib/seam.sh"

case "${1:-}" in
  -h|--help)
    sed -n '2,/^# Exit:/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0 ;;
esac

repo_args=()
for dir in "$STRONGHOLD"/*/; do
  dir="${dir%/}"
  [ -e "$dir/.git" ] || continue
  [ -f "$dir/CLAUDE.md" ] || continue
  policy="$(seam_first_token "$dir/CLAUDE.md" "Worktree policy")"
  # One token (--repo=NAME=POLICY) so a name or policy starting with `-` cannot confuse the parser.
  repo_args+=("--repo=$(basename -- "$dir")=$policy")
done

export STRONGHOLD_DIR="$STRONGHOLD"
exec python3 "$SCRIPT_DIR/lib/wt_capacity.py" ${repo_args[@]+"${repo_args[@]}"} "$@"
