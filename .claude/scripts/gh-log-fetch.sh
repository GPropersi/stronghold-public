#!/usr/bin/env bash
# gh-log-fetch.sh — wrapper for `gh run view --log-failed` (and similar
# log-fetch invocations) that need a writable cache directory, with an optional
# distilled summary mode.
#
# Why this exists:
#   `gh run view --log-failed` writes a cache entry under ~/.cache/gh/ before
#   streaming the log. The Claude Code sandbox denies writes outside the
#   project allowlist, so the cache write fails with:
#       "operation not permitted: ~/.cache/gh/run-log-*.zip"
#   The PreToolUse `gh allowlist` hook also rejects `dangerouslyDisableSandbox: true`
#   on bare `gh ...` calls, and additional env vars between `GH_TOKEN=$(...)`
#   and `gh` break the strict `^GH_TOKEN=\$\(.+\)\s+gh\s` prefix check.
#   A raw `--log-failed` stream is also thousands of lines of setup/cleanup
#   around the few dozen that matter; `--summary` distills it.
#
# This wrapper:
#   1. Resolves the GitHub App token generator (same 3-tier order as gh-bot.sh)
#   2. Exports `--repo <slug>` as GH_APP_REPO so the generator picks THAT repo's
#      App installation (this script runs from ~/code, whose cwd-origin fallback
#      would otherwise pick the stronghold's installation)
#   3. Generates a fresh installation token and validates its `ghs_` prefix
#   4. Unsets the stale GH_TOKEN injected by Claude Code and re-exports the fresh one
#   5. Redirects gh's cache to a sandbox-writable directory
#   6. Forwards all args verbatim to `gh` — or, with --summary, saves the output
#      to $CACHE_DIR/log-failed-<random>.txt (owner-only dir), prints the gh-failure-summary.py
#      digest and the raw log path
#
# No secrets are hardcoded — the token is produced by an external generator
# script, resolved in order:
#   1. $GH_TOKEN_GENERATOR                              (explicit override)
#   2. <git-repo-root>/.claude/bot/generate-gh-token.sh (per-repo bot dir)
#   3. $CLAUDE_PROJECT_DIR/.claude/scripts/generate-gh-token.sh (tracked example-bot App)
#
# Usage:
#   .claude/scripts/gh-log-fetch.sh run view --job 12345 --repo OWNER/REPO --log-failed
#       raw passthrough: streams gh's full output
#   .claude/scripts/gh-log-fetch.sh --summary run view 12345 --repo OWNER/REPO --log-failed
#       distilled summary (failing jobs/tests/assertions, <= 150 lines) + "raw log: <path>"
#       (keeps the newest 20 raw logs in /tmp/claude/gh-cache)
#
# Exit codes:
#   4  No token generator found, or the resolved one is missing/not executable.
#   5  Token generator returned an invalid value.
#   other  gh's own exit code is propagated.

set -euo pipefail

CACHE_DIR="/tmp/claude/gh-cache"
mkdir -p "$CACHE_DIR"

SUMMARY=0
if [ "${1:-}" = "--summary" ]; then
  SUMMARY=1
  shift
fi

resolve_generator() {
  # 1. Explicit override.
  if [ -n "${GH_TOKEN_GENERATOR:-}" ]; then
    printf '%s' "$GH_TOKEN_GENERATOR"
    return 0
  fi
  # 2. Per-repo bot dir at the git root of the current working directory.
  local repo_root
  if repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" \
     && [ -x "$repo_root/.claude/bot/generate-gh-token.sh" ]; then
    printf '%s' "$repo_root/.claude/bot/generate-gh-token.sh"
    return 0
  fi
  # 3. Canonical tracked generator in the stronghold (single source of truth).
  local stronghold_gen="${CLAUDE_PROJECT_DIR:-$HOME/code}/.claude/scripts/generate-gh-token.sh"
  if [ -x "$stronghold_gen" ]; then
    printf '%s' "$stronghold_gen"
    return 0
  fi
  return 1
}

TOKEN_GENERATOR="$(resolve_generator)" || {
  echo "gh-log-fetch.sh: no GitHub App token generator found. Set \$GH_TOKEN_GENERATOR, add" >&2
  echo "                 <repo>/.claude/bot/generate-gh-token.sh, or the stronghold's .claude/scripts/generate-gh-token.sh." >&2
  exit 4
}

if [ ! -x "$TOKEN_GENERATOR" ]; then
  echo "gh-log-fetch.sh: token generator not found or not executable: $TOKEN_GENERATOR" >&2
  exit 4
fi

# Forward --repo/-R to the generator so it resolves that repo's App installation.
previous_arg=""
for arg in "$@"; do
  case "$previous_arg" in
    --repo|-R) export GH_APP_REPO="$arg" ;;
  esac
  case "$arg" in
    --repo=*) export GH_APP_REPO="${arg#--repo=}" ;;
  esac
  previous_arg="$arg"
done

# Drop the stale GH_TOKEN injected by Claude Code's settings before generating
# a fresh App token so nothing downstream can accidentally use the stale value.
unset GH_TOKEN

APP_TOKEN="$("$TOKEN_GENERATOR")"

if [ -z "$APP_TOKEN" ] || [ "${APP_TOKEN:0:4}" != "ghs_" ]; then
  echo "gh-log-fetch.sh: token generator did not return a valid installation token (expected 'ghs_' prefix)." >&2
  exit 5
fi

export GH_TOKEN="$APP_TOKEN"
export XDG_CACHE_HOME="$CACHE_DIR"

if [ "$SUMMARY" -eq 0 ]; then
  exec gh "$@"
fi

SUMMARIZER="$(dirname "$0")/gh-failure-summary.py"
python3 "$SUMMARIZER" --prune-dir "$CACHE_DIR" --keep 20

# Raw logs can hold values GitHub failed to mask: keep the dir owner-only and
# use an unpredictable, freshly created file (mktemp never follows a planted
# symlink; X's stay last for BSD/macOS mktemp, then the .txt suffix is added).
chmod 700 "$CACHE_DIR"
RAW_TMP="$(mktemp "$CACHE_DIR/log-failed-XXXXXXXX")"
RAW_LOG="$RAW_TMP.txt"
mv "$RAW_TMP" "$RAW_LOG"
gh_status=0
gh "$@" > "$RAW_LOG" || gh_status=$?
if [ "$gh_status" -ne 0 ]; then
  echo "gh-log-fetch.sh: gh exited $gh_status (raw output: $RAW_LOG)" >&2
  exit "$gh_status"
fi

python3 "$SUMMARIZER" "$RAW_LOG"
echo "raw log: $RAW_LOG"
