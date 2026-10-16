#!/usr/bin/env bash
#
# repo-facts.sh — ONE cross-repo metadata digest for the stronghold + every sub-repo.
#
# Why this exists: the `## Claude Config` seam is per-repo by design, so answering
# "what is every repo's default branch / test command / bot identity" has meant one
# grep per repo per question. Transcripts show that shape dominating real sessions
# (four separate greps just to read four Makefiles' targets). Every one of those is
# a full round-trip, and a round-trip costs ~100k tokens of re-read context — three
# orders of magnitude more than the answer itself. This collapses the whole class
# into a single call.
#
# NOT CACHED, BY DESIGN. Reading a handful of CLAUDE.md + Makefile files takes
# milliseconds, so there is no index file to write, invalidate, or refresh on edit
# — it cannot go stale because it is regenerated every time it is asked for. The
# staleness problem only appears once you cache; don't cache what is this cheap.
#
# Code structure (routes, symbols, call chains) deliberately stays OUT of this:
# that data is large, churns constantly, is always queried repo-scoped, and each
# sub-repo has its own independent git HEAD. Metadata is the layer that is small,
# stable, and genuinely queried ACROSS repos — so metadata is what gets unified.
#
# Usage:
#   repo-facts.sh              every repo
#   repo-facts.sh app-main     one repo
#   repo-facts.sh --commands   commands + make targets only (the most-asked slice)
#
# Env:
#   STRONGHOLD_DIR   override the stronghold root to scan (test seam; default: this script's checkout)
#
# Each repo also lists its linked worktrees (`git worktree list`), since `find -type d`
# can't see them (their .git is a file). Skipped under --commands. A repo whose seam declares
# any `Worktree *` key also gets one `worktree policy:` line (policy, ports, setup target).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STRONGHOLD="${STRONGHOLD_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
PLANS_DIR="$STRONGHOLD/plans"
# shellcheck source=lib/seam.sh
. "$SCRIPT_DIR/lib/seam.sh"

repo_filter=""
commands_only=0
for arg in "$@"; do
  case "$arg" in
    -h|--help)
      sed -n '2,33p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    --commands) commands_only=1 ;;
    --*) echo "repo-facts.sh: unknown option '$arg'" >&2; exit 2 ;;
    *) repo_filter="$arg" ;;
  esac
done

# Pull one `- **Key:** value` scalar out of a CLAUDE.md's Claude Config seam.
# Values carry trailing prose/HTML comments for humans; strip them so the digest
# stays compact — a digest nobody can afford to read has defeated its own purpose.
# Isolate a CLAUDE.md's `## Claude Config` seam. Fenced code blocks are stripped
# FIRST: the stronghold's own CLAUDE.md documents the seam *schema* inside a
# ```markdown fence, and without this the digest happily reports `<owner>/<repo>`
# and `<cmd>` as if they were that repo's real settings.
#
# Named `facts_seam_section` (not `seam_section`) so it does not shadow lib/seam.sh's reader,
# which `seam_raw` calls: the two sections end differently (this one at `^## [^C]`, the lib's
# at any `## `), and `seam_val`'s truncating behaviour is deliberately left as it was.
facts_seam_section() {
  local file="$1"
  [ -f "$file" ] || return 0
  awk '/^[[:space:]]*```/ { fence = !fence; next } !fence' "$file" 2>/dev/null \
    | sed -n '/^## Claude Config/,/^## [^C]/p' || true
}

seam_val() {
  local file="$1" key="$2"
  [ -f "$file" ] || return 0
  # SC2016: the `\1` below is a sed backreference, not a shell variable — single
  # quotes are required here, not a mistake.
  # shellcheck disable=SC2016
  facts_seam_section "$file" \
    | grep -m1 -E "^- \*\*${key}:\*\*" 2>/dev/null \
    | sed -E "s/^- \*\*${key}:\*\* *//" \
    | sed -E 's/<!--.*$//' \
    | sed -E 's/^`([^`]*)`.*$/\1/' \
    | sed -E 's/  +\(.*$//' \
    | awk '{ if (length($0) > 100) print substr($0, 1, 99) "…"; else print }' \
    | sed -E 's/[[:space:]]+$//' || true
}

# The `| Purpose | Command |` rows of the seam's Commands table.
seam_commands() {
  local file="$1"
  [ -f "$file" ] || return 0
  facts_seam_section "$file" \
    | sed -n '/\*\*Commands:\*\*/,/^- \*\*/p' \
    | grep -E '^[[:space:]]*\|' \
    | grep -vE '^\s*\|[[:space:]-]*\|[[:space:]-]*\|?\s*$' \
    | grep -viE '^\s*\|\s*Purpose\s*\|' \
    | sed -E 's/^[[:space:]]*\|[[:space:]]*([^|]*[^|[:space:]])[[:space:]]*\|[[:space:]]*(.*[^[:space:]])[[:space:]]*\|.*$/\1 = \2/' \
    | sed -E 's/`//g' \
    | awk '{ if (length($0) > 110) print substr($0, 1, 109) "…"; else print }' || true
}

# Print a `worktrees:` block for the LINKED worktrees of the repo at $1 (nothing when
# there are none). `git worktree list --porcelain` emits one blank-line-terminated
# block per worktree, the primary checkout first — that first block is skipped.
worktree_lines() {
  local dir="$1" key val
  local path="" branch="" detached=0 prunable=0 seen_primary=0 printed_header=0
  local shown label real_dir
  # git reports physical paths, so relativize against the physical $dir (a symlinked
  # path or trailing slash would otherwise defeat the prefix strip below).
  real_dir="$(cd "$dir" 2>/dev/null && pwd -P)" || real_dir="$dir"
  real_dir="${real_dir%/}"
  while read -r key val; do
    case "$key" in
      worktree) path="$val" ;;
      branch) branch="${val#refs/heads/}" ;;
      detached) detached=1 ;;
      prunable) prunable=1 ;;
      "")
        if [ -n "$path" ]; then
          if [ "$seen_primary" -eq 0 ]; then
            seen_primary=1
          else
            if [ "$printed_header" -eq 0 ]; then
              echo "worktrees:"
              printed_header=1
            fi
            shown="${path#"$real_dir"/}"
            if [ "$detached" -eq 1 ]; then
              label="(detached)"
            elif [ -n "$branch" ]; then
              label="[$branch]"
            else
              label="(no branch)"
            fi
            if [ "$prunable" -eq 1 ]; then label="$label (prunable)"; fi
            echo "  $shown  $label"
          fi
        fi
        path="" branch="" detached=0 prunable=0 ;;
    esac
  done < <({ git -C "$dir" worktree list --porcelain 2>/dev/null || true; echo; })
  return 0
}

# One `worktree policy:` line when the seam declares ANY `Worktree *` key (unset, empty and
# `n/a` all read as unset, and keys inside a code fence are ignored by lib/seam.sh).
worktree_policy_line() {
  local md="$1" key any="" policy ports setup
  for key in link setup teardown ports policy 'allowed targets' 'guarded targets'; do
    any="$any$(seam_raw "$md" "Worktree $key")"
  done
  [ -n "$any" ] || return 0
  policy="$(seam_raw "$md" 'Worktree policy')"
  ports="$(seam_raw "$md" 'Worktree ports')"
  setup="$(seam_raw "$md" 'Worktree setup')"
  echo "worktree policy: ${policy:-n/a} ports=${ports:-n/a} setup=${setup:-n/a}"
}

emit() {
  local name="$1" dir="$2"
  local md="$dir/CLAUDE.md"

  local slug default_branch cur stack bot runtime appurl reviewer
  slug="$(seam_val "$md" 'Repo slug')"
  default_branch="$(seam_val "$md" 'Default branch')"
  stack="$(seam_val "$md" 'Stack')"
  bot="$(seam_val "$md" 'Bot identity')"
  runtime="$(seam_val "$md" 'Container runtime')"
  appurl="$(seam_val "$md" 'App URL \(Playwright MCP\)')"
  reviewer="$(seam_val "$md" 'PR reviewer')"

  cur="$(git -C "$dir" branch --show-current 2>/dev/null || true)"
  [ -z "$cur" ] && cur='(detached/none)'

  local plans_open=0
  if [ -d "$PLANS_DIR/$name/open" ]; then
    plans_open="$(find "$PLANS_DIR/$name/open" -mindepth 1 -maxdepth 2 -type f -name '*.md' 2>/dev/null | wc -l | tr -d ' ')"
  fi

  local targets=''
  if [ -f "$dir/Makefile" ]; then
    targets="$(grep -hE '^[a-zA-Z0-9_.-]+:.*##' "$dir/Makefile" 2>/dev/null | cut -d: -f1 | tr '\n' ' ' | sed -E 's/[[:space:]]+$//' || true)"
  fi

  echo
  if [ -n "$slug" ]; then
    echo "## $name  ($slug)"
  else
    echo "## $name"
  fi

  if [ "$commands_only" -eq 0 ]; then
    local line="branch: $cur"
    [ -n "$default_branch" ] && [ "$default_branch" != "$cur" ] && line="$line  (default: $default_branch)"
    line="$line  ·  open plans: $plans_open"
    echo "$line"
    worktree_lines "$dir"
    worktree_policy_line "$md"
    [ -n "$stack" ] && echo "stack: $stack"
    [ -n "$runtime" ] && echo "runtime: $runtime"
    [ -n "$appurl" ] && echo "app url: $appurl"
    if [ -n "$bot" ]; then echo "bot: $bot"; else echo "bot: n/a (no seam entry)"; fi
    [ -n "$reviewer" ] && echo "reviewer: $reviewer"
    [ ! -f "$md" ] && echo "!! no CLAUDE.md — no ## Claude Config seam for generic skills to read"
  fi

  local cmds
  cmds="$(seam_commands "$md")"
  if [ -n "$cmds" ]; then
    echo "commands:"
    printf '%s\n' "$cmds" | sed 's/^/  /'
  fi
  [ -n "$targets" ] && echo "make: $targets"

  # Explicit success: the last test above returns 1 for a repo with no Makefile
  # (app-bot), which under `set -e` would abort the whole sweep mid-listing.
  return 0
}

echo "# Repo Facts"
echo "Generated fresh on every run — never cached, so it cannot be stale."

if [ -z "$repo_filter" ] || [ "$repo_filter" = "stronghold" ]; then
  emit stronghold "$STRONGHOLD"
fi

while IFS= read -r gitdir; do
  dir="$(dirname "$gitdir")"
  name="$(basename "$dir")"
  if [ -n "$repo_filter" ] && [ "$repo_filter" != "$name" ]; then
    continue
  fi
  emit "$name" "$dir"
done < <(find "$STRONGHOLD" -mindepth 2 -maxdepth 2 -name .git -type d | sort)
