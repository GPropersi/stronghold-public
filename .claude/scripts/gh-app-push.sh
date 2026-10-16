#!/bin/bash
# gh-app-push.sh — safely push a branch to the current repo as its GitHub App bot.
#
# Repo-agnostic: the target repo is derived from the current repo's `origin`
# remote (override with $GH_APP_REPO_URL). The token generator is auto-resolved
# per repo (override $GH_TOKEN_GENERATOR, else <git-root>/.claude/bot/generate-gh-token.sh,
# else the tracked stronghold generator .claude/scripts/generate-gh-token.sh) — so a sub-repo
# with its own App needs no env override. See .claude/TIER-B-ROADMAP.md.
#
# Why this exists:
#   The project's .claude/settings.local.json injects a stale GH_TOKEN (a PAT
#   belonging to user app-bot) into every Bash process's environment. The
#   conventional one-liner
#       GH_TOKEN=$(token-gen) git ... "https://x-access-token:$GH_TOKEN@..."
#   has a bash evaluation bug: the parent shell expands $GH_TOKEN inside the
#   URL BEFORE the command-prefix assignment takes effect, so git pushes with
#   the stale PAT — not the fresh App token. This script sidesteps the issue
#   by storing the fresh token in a local variable named APP_TOKEN (so nothing
#   in the environment can shadow it) and unsetting GH_TOKEN so no subprocess
#   accidentally inherits the stale PAT.
#
#   The token is also never embedded in the remote URL — it's handed to git via
#   GIT_ASKPASS (gh-app-askpass.sh, reading APP_TOKEN). A URL-embedded token
#   leaks in cleartext through git's own upstream-tracking message, which is
#   visible to whatever ran the command (e.g. an agent's tool output).
#
# Why the push targets the `origin` REMOTE, not a bare URL:
#   Pushing to a bare URL (`git push -u https://host/owner/repo.git <branch>`)
#   never creates refs/remotes/origin/<branch>. Consequences, all observed:
#     * `git branch --set-upstream-to=origin/<branch>` fails outright with
#       "fatal: the requested upstream branch 'origin/<branch>' does not exist"
#       until someone manually runs `git fetch origin <branch>`.
#     * `-u` against a URL records the URL itself as branch.<b>.remote, which
#       git cannot resolve — the branch reads as having NO upstream at all.
#     * With no upstream, a later `git fetch -p` cannot mark the branch
#       ": gone]" once its PR merges, so the gmas / `git cleanup` branch
#       reaper never lists it as a cleanup candidate.
#   Pushing to the remote NAME with a per-invocation remote.origin.pushurl
#   override keeps the App-token HTTPS transport while letting git update the
#   remote-tracking ref and record upstream in the same step.
#
# Usage:
#   .claude/scripts/gh-app-push.sh [branch-name]
#
# Arguments:
#   branch-name  Optional. Defaults to the current branch. Must not be
#                main or master. Must not contain any force-push flag.
#
# Exit codes:
#   0  Push succeeded.
#   2  Bad arguments (too many, or detached HEAD with no branch given).
#   3  Refused: target is main/master or a force flag was supplied.
#   4  Token generator missing or not executable.
#   5  Token generator returned an invalid value.
#   6  Refused: no passing push-gate result for this branch at HEAD.
#   other  git push failed (its own exit code is propagated).

set -euo pipefail

# Directory this script lives in — used to locate the askpass helper beside it.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ASKPASS_HELPER="$SCRIPT_DIR/gh-app-askpass.sh"

# Normalize a remote URL to host/owner/repo.git form. Handles both SSH
# (git@host:owner/repo[.git]) and HTTPS (https://host/owner/repo[.git]) forms,
# and is idempotent so an already-normalized $GH_APP_REPO_URL passes through.
normalize_repo_url() {
  local u="${1:-}"
  if [ -z "$u" ]; then
    return 0
  fi
  u="${u%.git}"     # drop trailing .git
  u="${u#git@}"     # strip git@ (SSH form)
  u="${u#*://}"     # strip scheme (https:// / ssh://)
  u="${u#*@}"       # strip any remaining userinfo@
  u="$(printf '%s' "$u" | sed 's|:|/|')"  # host:owner/repo -> host/owner/repo
  printf '%s.git' "$u"
}

ORIGIN_URL="$(git remote get-url origin 2>/dev/null || true)"
ORIGIN_NORM="$(normalize_repo_url "$ORIGIN_URL")"

# Target repo URL (host/path.git form). Priority: $GH_APP_REPO_URL, else the
# current repo's `origin` remote.
if [ -n "${GH_APP_REPO_URL:-}" ]; then
  REPO_URL="$(normalize_repo_url "$GH_APP_REPO_URL")"
else
  REPO_URL="$ORIGIN_NORM"
fi

if [ -z "$REPO_URL" ] || [ "$REPO_URL" = ".git" ]; then
  echo "Error: could not determine target repo. Set GH_APP_REPO_URL or add an 'origin' remote." >&2
  exit 2
fi

# Per-repo GitHub App token generator (each repo's bot may be a distinct App).
# Priority: $GH_TOKEN_GENERATOR, else <git-root>/.claude/bot/generate-gh-token.sh,
# else the tracked stronghold generator .claude/scripts/generate-gh-token.sh.
TOKEN_GENERATOR="${GH_TOKEN_GENERATOR:-}"
if [ -z "$TOKEN_GENERATOR" ]; then
  _repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
  if [ -n "$_repo_root" ] && [ -x "$_repo_root/.claude/bot/generate-gh-token.sh" ]; then
    TOKEN_GENERATOR="$_repo_root/.claude/bot/generate-gh-token.sh"
  else
    # Canonical tracked generator in the stronghold (single source of truth).
    TOKEN_GENERATOR="${CLAUDE_PROJECT_DIR:-$HOME/code}/.claude/scripts/generate-gh-token.sh"
  fi
fi

if [ $# -gt 1 ]; then
  echo "Error: too many arguments. Usage: $0 [branch-name]" >&2
  exit 2
fi

BRANCH="${1:-$(git branch --show-current)}"

if [ -z "$BRANCH" ]; then
  echo "Error: no branch name given and git branch --show-current is empty (detached HEAD?)." >&2
  exit 2
fi

case "$BRANCH" in
  main | master)
    echo "Error: refusing to push to '$BRANCH'. Feature branches only." >&2
    exit 3
    ;;
  --force | -f | --force-with-lease | --force-with-lease=* | *\ --force* | *\ -f*)
    echo "Error: branch name looks like a force-push flag: '$BRANCH'. Refused." >&2
    exit 3
    ;;
esac

# Push gate: refuse unless a passing result exists for the exact HEAD sha
# (see .claude/scripts/push-gate.sh).
python3 "$SCRIPT_DIR/lib/push_gate.py" check "$BRANCH" || exit 6

if [ ! -x "$TOKEN_GENERATOR" ]; then
  echo "Error: token generator not found or not executable: $TOKEN_GENERATOR" >&2
  exit 4
fi

if [ ! -x "$ASKPASS_HELPER" ]; then
  echo "Error: askpass helper not found or not executable: $ASKPASS_HELPER" >&2
  exit 4
fi

# Route the push through the `origin` remote whenever origin already points at
# the repo we're targeting — that is what makes git update
# refs/remotes/origin/<branch> and record upstream tracking (see header).
PUSH_VIA_ORIGIN=0
if [ -n "$ORIGIN_NORM" ] && [ "$REPO_URL" = "$ORIGIN_NORM" ]; then
  PUSH_VIA_ORIGIN=1
fi

# `git -c remote.origin.pushurl=...` APPENDS to a multi-valued key, so if the
# repo already configures a pushurl we would push to every configured
# destination. Rare, but silently wrong — fall back to the bare-URL push.
if [ "$PUSH_VIA_ORIGIN" -eq 1 ] && [ -n "$(git config --get-all remote.origin.pushurl 2>/dev/null || true)" ]; then
  echo "Note: remote.origin.pushurl is already configured; pushing by URL instead." >&2
  PUSH_VIA_ORIGIN=0
fi

# Remove the stale GH_TOKEN injected by Claude Code so nothing downstream can
# accidentally use it. We deliberately do NOT name our own variable GH_TOKEN.
unset GH_TOKEN

APP_TOKEN=$("$TOKEN_GENERATOR")

if [ -z "$APP_TOKEN" ] || [ "${APP_TOKEN:0:4}" != "ghs_" ]; then
  echo "Error: token generator did not return a valid installation token (expected 'ghs_' prefix)." >&2
  exit 5
fi

echo "Pushing '$BRANCH' to $REPO_URL as the GitHub App bot..."

# The token is handed to git via GIT_ASKPASS (gh-app-askpass.sh, which reads it
# from APP_TOKEN) rather than embedded in the remote URL. A URL-embedded token
# leaks in cleartext through git's own upstream-tracking message ("branch ...
# set up to track 'https://x-access-token:<TOKEN>@...'"), visible to whatever
# ran the command (e.g. an agent's tool output), not just whoever's at the
# keyboard. credential.helper= disables the osxkeychain helper so GIT_ASKPASS is
# the only credential source; GIT_TERMINAL_PROMPT=0 prevents interactive fallback.
export APP_TOKEN
if [ "$PUSH_VIA_ORIGIN" -eq 1 ]; then
  GIT_ASKPASS="$ASKPASS_HELPER" GIT_TERMINAL_PROMPT=0 git \
    -c credential.helper= \
    -c "remote.origin.pushurl=https://${REPO_URL}" \
    push -u origin \
    "$BRANCH"
else
  # No usable `origin` for this target: push by URL. `-u` is deliberately
  # omitted — against a URL it records an unresolvable upstream (see header).
  GIT_ASKPASS="$ASKPASS_HELPER" GIT_TERMINAL_PROMPT=0 git \
    -c credential.helper= \
    push "https://${REPO_URL}" \
    "$BRANCH"
fi
unset APP_TOKEN

if [ "$PUSH_VIA_ORIGIN" -eq 1 ]; then
  # Safety net: if origin carries a non-standard (or missing) fetch refspec the
  # push won't have updated the tracking ref. Point it at what we just pushed.
  if ! git rev-parse --verify --quiet "refs/remotes/origin/${BRANCH}" >/dev/null; then
    git update-ref "refs/remotes/origin/${BRANCH}" "refs/heads/${BRANCH}"
  fi

  # `git push -u` normally records upstream already; re-assert it only if it
  # didn't stick (e.g. a sandbox-blocked .git/config write).
  if [ "$(git for-each-ref --format='%(upstream:short)' "refs/heads/${BRANCH}")" = "origin/${BRANCH}" ]; then
    echo "Upstream tracking set: origin/${BRANCH}"
  elif git branch --set-upstream-to="origin/${BRANCH}" "$BRANCH" >/dev/null 2>&1; then
    echo "Upstream tracking set: origin/${BRANCH}"
  else
    echo "Warning: refs/remotes/origin/${BRANCH} exists, but upstream tracking could not be" >&2
    echo "recorded — most likely a sandbox-blocked .git/config write. Without it a later" >&2
    echo "'git fetch -p' cannot mark this branch ': gone]' for the branch reaper. Fix with" >&2
    echo "dangerouslyDisableSandbox: true:" >&2
    echo "  git branch --set-upstream-to=origin/${BRANCH} ${BRANCH}" >&2
  fi
else
  echo "Note: pushed by URL because 'origin' does not point at ${REPO_URL}." >&2
  echo "No remote-tracking ref or upstream was recorded for ${BRANCH}." >&2
fi

echo "Push complete: $BRANCH"
