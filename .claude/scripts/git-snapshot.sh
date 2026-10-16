#!/usr/bin/env bash
#
# git-snapshot.sh — one read-only call that replaces the usual `git status` / `git diff --stat` /
# `git log` / `git branch --show-current` sequence.
#
# Usage:
#   git-snapshot.sh [<dir>] [--base <ref>] [--max <N>]
#
#   <dir>        a checkout (default: the current directory)
#   --base <ref> also show commits and diffstat of <ref>...HEAD (e.g. --base origin/main)
#   --max <N>    cap on listed status/diffstat/commit lines (default 30)
#
# Prints: branch (or detached sha), HEAD, upstream ahead/behind, a staged/unstaged/untracked
# status summary, the changed paths, staged and unstaged diffstats, the last 5 commits, and
# (with --base) the commits and diffstat against the base. Nothing is written: git runs with
# GIT_OPTIONAL_LOCKS=0 and only read-only subcommands.
#
# Exit: 0 ok | 2 usage error | 3 not a git work tree
set -euo pipefail

die() {
  echo "git-snapshot.sh: $1" >&2
  exit "${2:-2}"
}

dir="."
base=""
max=30
dir_set=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h | --help)
      sed -n '2,/^set /{/^set /!p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    --base)
      [ "$#" -ge 2 ] || die "--base needs a ref"
      base="$2"
      shift 2
      ;;
    --max)
      [ "$#" -ge 2 ] || die "--max needs a number"
      max="$2"
      shift 2
      ;;
    -*) die "unknown option '$1'" ;;
    *)
      [ "$dir_set" -eq 0 ] || die "unexpected argument '$1'"
      dir="$1"
      dir_set=1
      shift
      ;;
  esac
done
case "$max" in '' | *[!0-9]*) die "--max must be a number" ;; esac

export GIT_OPTIONAL_LOCKS=0
g() { git -C "$dir" "$@"; }

g rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "not a git work tree: $dir" 3

top="$(g rev-parse --show-toplevel)"
branch="$(g branch --show-current)"
head_sha="$(g rev-parse --short HEAD 2>/dev/null || echo '(no commits)')"
head_subject="$(g log -1 --format=%s 2>/dev/null || true)"

if [ -n "$branch" ]; then
  echo "repo: $top"
  echo "branch: $branch"
else
  echo "repo: $top"
  echo "branch: (detached at $head_sha)"
fi
echo "head: $head_sha $head_subject"

if upstream="$(g rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null)"; then
  counts="$(g rev-list --left-right --count "$upstream...HEAD" 2>/dev/null || echo '0 0')"
  behind="${counts%%[[:space:]]*}"
  ahead="${counts##*[[:space:]]}"
  echo "upstream: $upstream (ahead $ahead, behind $behind)"
else
  echo "upstream: none"
fi

status="$(g status --short --untracked-files=normal)"
if [ -z "$status" ]; then
  echo "status: clean"
else
  staged="$(printf '%s\n' "$status" | awk '!/^\?\?/ && substr($0,1,1) != " " {n++} END {print n+0}')"
  unstaged="$(printf '%s\n' "$status" | awk '!/^\?\?/ && substr($0,2,1) != " " {n++} END {print n+0}')"
  untracked="$(printf '%s\n' "$status" | awk '/^\?\?/ {n++} END {print n+0}')"
  total="$(printf '%s\n' "$status" | wc -l | tr -d ' ')"
  echo "status: $total changed ($staged staged, $unstaged unstaged, $untracked untracked)"
  printf '%s\n' "$status" | head -n "$max" | sed 's/^/  /'
  [ "$total" -le "$max" ] || echo "  ... ($((total - max)) more)"
fi

show_stat() {
  local label="$1"
  shift
  local stat
  stat="$(g diff "$@" --stat 2>/dev/null || true)"
  if [ -n "$stat" ]; then
    echo "$label:"
    printf '%s\n' "$stat" | tail -n "$((max + 1))" | sed 's/^/  /'
  fi
}
show_stat "staged diffstat" --cached
show_stat "unstaged diffstat"

echo "recent commits:"
g log --oneline -n 5 2>/dev/null | sed 's/^/  /' || true

if [ -n "$base" ]; then
  g rev-parse --verify --quiet "$base^{commit}" >/dev/null || die "unknown base ref: $base"
  n_ahead="$(g rev-list --count "$base..HEAD")"
  echo "vs $base: $n_ahead commit(s) ahead"
  g log --oneline "$base..HEAD" 2>/dev/null | head -n "$max" | sed 's/^/  /'
  stat="$(g diff --stat "$base...HEAD" 2>/dev/null || true)"
  if [ -n "$stat" ]; then
    echo "diffstat $base...HEAD:"
    printf '%s\n' "$stat" | tail -n "$((max + 1))" | sed 's/^/  /'
  fi
fi
