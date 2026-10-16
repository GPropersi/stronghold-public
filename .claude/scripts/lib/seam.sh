# shellcheck shell=bash
# seam.sh — shared, SOURCED reader for the `## Claude Config` seam of a repo's CLAUDE.md.
# Never executed directly: no top-level output, no set -e. Bash-3.2-safe (macOS): no mapfile,
# no associative arrays. Mirrors lib/repo-root.sh in shape.
#
# Consumers: wt.sh (the `Worktree *` keys) and repo-facts.sh (policy line).
#
# Differences from repo-facts.sh's own `seam_val` (which is deliberately left untouched):
#   - values are never truncated (the `Worktree link` list can run well past 100 chars);
#   - the section ends at ANY next `## ` heading, not just one that does not start with `C`;
#   - `n/a` and an empty value both read as "unset" (nothing printed).
# Both readers ignore fenced code blocks, so the root CLAUDE.md's template (which carries
# placeholder `Worktree *` keys inside a fence) never reads as real configuration.

# seam_section <claude-md> — the `## Claude Config` section, outside code fences, up to and
# including the next `## ` heading line (harmless: it never matches a `- **Key:**` bullet).
# Prints nothing when the file is missing or has no such section.
seam_section() {
  [ -f "${1:-}" ] || return 0
  awk '/^[[:space:]]*```/ { fence = !fence; next } !fence' "$1" \
    | sed -n '/^## Claude Config$/,/^## /p'
}

# seam_raw <claude-md> <key> — the value of the first `- **<key>:** ...` bullet in the seam:
# a trailing `<!-- ... -->` comment stripped, ONE surrounding backtick pair unwrapped, trimmed.
# Prints nothing for a missing file/key, an empty value, or `n/a`. Always returns 0.
seam_raw() {
  [ -f "${1:-}" ] || return 0
  local value
  # No early `exit` in awk: with pipefail an early exit could SIGPIPE the upstream stage.
  value="$(seam_section "$1" \
    | awk -v prefix="- **${2:-}:**" \
      '!found && index($0, prefix) == 1 { print substr($0, length(prefix) + 1); found = 1 }')"
  value="$(printf '%s' "$value" \
    | sed -E 's/[[:space:]]*<!--.*-->[[:space:]]*$//; s/^[[:space:]]+//; s/[[:space:]]+$//; s/^`(.*)`$/\1/; s/^[[:space:]]+//; s/[[:space:]]+$//')"
  if [ -n "$value" ] && [ "$value" != "n/a" ]; then
    printf '%s\n' "$value"
  fi
  return 0
}

# seam_first_token <claude-md> <key> — the first backtick token of the key's value, else its first
# whitespace token (trailing prose and a `<!-- ... -->` comment ignored). Prints nothing for a
# missing file/key, an empty value, or `n/a`. Always returns 0. Unlike seam_raw it never returns
# the trailing prose of "`value` then prose".
seam_first_token() {
  [ -f "${1:-}" ] || return 0
  local value token
  value="$(seam_section "$1" \
    | awk -v prefix="- **${2:-}:**" \
      '!found && index($0, prefix) == 1 { print substr($0, length(prefix) + 1); found = 1 }')"
  value="$(printf '%s' "$value" \
    | sed -E 's/[[:space:]]*<!--.*-->[[:space:]]*$//; s/^[[:space:]]+//; s/[[:space:]]+$//')"
  case "$value" in
    *'`'*'`'*) token="$(printf '%s' "$value" | sed -E 's/^[^`]*`([^`]*)`.*$/\1/')" ;;
    *) token="$(printf '%s' "$value" | awk '{ print $1 }')" ;;
  esac
  if [ -n "$token" ] && [ "$token" != "n/a" ]; then
    printf '%s\n' "$token"
  fi
  return 0
}

# seam_keys <claude-md> — one column-0 `- **Key:**` name per line from the seam section
# (fenced blocks excluded, indented bullets excluded). Always returns 0.
seam_keys() {
  seam_section "${1:-}" | sed -n -E 's/^- \*\*([^*]+):\*\*.*$/\1/p'
  return 0
}

# seam_has_section <claude-md> — exit 0 when a real (unfenced) `## Claude Config` heading exists.
seam_has_section() {
  [ -n "$(seam_section "${1:-}")" ]
}

# seam_plans_bucket <stronghold-root> <repo-dir> — the directory under <root>/plans/ holding the
# repo's plans, from the `Plans bucket` seam key of <root>/<repo-dir>/CLAUDE.md. The literal
# `stronghold` maps to itself. There is NO fallback to the slug or the directory name: a missing
# key prints `missing seam key "Plans bucket" in <path>` to stderr and returns 1.
seam_plans_bucket() {
  local root="${1:-}" repo="${2:-}" md bucket
  if [ "$repo" = "stronghold" ]; then
    printf 'stronghold\n'
    return 0
  fi
  md="$root/$repo/CLAUDE.md"
  bucket="$(seam_first_token "$md" "Plans bucket")"
  if [ -z "$bucket" ]; then
    printf 'missing seam key "Plans bucket" in %s\n' "$md" >&2
    return 1
  fi
  printf '%s\n' "$bucket"
}
