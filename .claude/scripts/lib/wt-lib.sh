# shellcheck shell=bash
# wt-lib.sh — shared, SOURCED helpers for the worktree lifecycle (wt.sh): branch -> slug
# sanitizers, the compose project name, and primary-checkout detection.
# Never executed directly: no top-level output, no set -e. Bash-3.2-safe (macOS).
#
# LOCKSTEP: `wt_slug_dns` must stay identical to the DNS-safe slug in app-main/Makefile:75
# (lower-case, every char outside [a-z0-9-] -> `-`, leading `-` stripped, cut to 40 chars,
# trailing `-` stripped). Repeated separators are intentionally NOT collapsed (parity).
# Only the DNS form is lifted; `wt_slug_ident` exists so tests can prove the two sanitizers
# agree on the shared vectors. Repos derive their own DB identifiers.
#
# Every function uses `printf '%s'` (never `echo`/`eval`), so a quote in a branch is data.
# The slug functions print the slug WITHOUT a trailing newline (callers use $(...)).
# LC_ALL=C pins tr/cut to bytes so a multi-byte branch name maps to dashes deterministically.

_WT_LIB_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=lib/repo-root.sh
. "$_WT_LIB_DIR/repo-root.sh"

# _wt_dns_chars <text> — lower-case, non-[a-z0-9-] -> `-`, leading `-` stripped. No length cap.
_wt_dns_chars() {
  printf '%s' "${1:-}" | LC_ALL=C tr '[:upper:]' '[:lower:]' | LC_ALL=C tr -c 'a-z0-9-' '-' | sed 's/^-*//'
}

# wt_slug_dns <branch> — the DNS-safe slug (<= 40 chars). Empty result: message, return 1.
wt_slug_dns() {
  local slug
  slug="$(_wt_dns_chars "${1:-}" | LC_ALL=C cut -c1-40 | sed 's/-*$//')"
  if [ -z "$slug" ]; then
    printf 'wt: cannot derive a worktree slug from branch "%s"\n' "${1:-}" >&2
    return 1
  fi
  printf '%s' "$slug"
}

# wt_slug_ident <branch> — the identifier-safe form: lower-case, non-[a-z0-9_] -> `_`, cut to
# 55 chars, NOT stripped. Empty input: message, return 1.
wt_slug_ident() {
  local ident
  ident="$(printf '%s' "${1:-}" | LC_ALL=C tr '[:upper:]' '[:lower:]' | LC_ALL=C tr -c 'a-z0-9_' '_' | LC_ALL=C cut -c1-55)"
  if [ -z "$ident" ]; then
    printf 'wt: cannot derive an identifier from "%s"\n' "${1:-}" >&2
    return 1
  fi
  printf '%s' "$ident"
}

# wt_project_name <repo> <slug> — the compose project name `<repo>-<slug>`, lower-cased and
# DNS-sanitized, capped at 63 chars (no trailing `-`). The repo part is sanitized without the
# 40-char cap that `wt_slug_dns` applies, so the slug part is never truncated by a long repo
# name before the final 63-char cap.
wt_project_name() {
  local repo_part slug_part name
  repo_part="$(_wt_dns_chars "${1:-}")"
  slug_part="$(wt_slug_dns "${2:-}")" || return 1
  if [ -z "$repo_part" ]; then
    printf 'wt: cannot derive a compose project name from repo "%s"\n' "${1:-}" >&2
    return 1
  fi
  name="$(printf '%s-%s' "$repo_part" "$slug_part" | LC_ALL=C cut -c1-63 | sed 's/-*$//')"
  printf '%s' "$name"
}

# wt_primary_root <path> — the primary checkout containing <path> (see repo-root.sh).
wt_primary_root() {
  repo_primary_root "${1:-}"
}

# wt_is_primary <path> — returns 0 iff <path> is not inside a linked worktree.
wt_is_primary() {
  ! repo_is_linked_worktree "${1:-}"
}
