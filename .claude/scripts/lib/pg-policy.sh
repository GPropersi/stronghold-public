#!/usr/bin/env bash
#
# pg-policy.sh — Worktree-policy verdict for one push-gate suite command.
# Usage: bash pg-policy.sh <command> <checkout-toplevel>
# Prints nothing to allow, or the one reason line (as the live-resource guard words it) to deny.
# A suite command that is not a plain `make` invocation cannot be policy-evaluated and is allowed.
# Always exits 0; the caller decides from the output.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/worktree-guard.sh
. "$SCRIPT_DIR/worktree-guard.sh"

[ "$#" -eq 2 ] || { echo "usage: pg-policy.sh <command> <checkout-toplevel>" >&2; exit 2; }

# Called bare so WTG_TARGETS survives; a non-zero return means an unsupported shape: allow.
if ! wtg_parse_make "$1" "$2"; then
  exit 0
fi
wtg_decide "$2" || true
