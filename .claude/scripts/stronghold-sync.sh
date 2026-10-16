#!/usr/bin/env bash
#
# stronghold-sync.sh — commit + push stronghold workflow changes to main.
#
# Called as the FINAL step of any central skill that writes to the stronghold
# plans store / changelog (plan-creator, master-plan-creator,
# master-plan-orchestrator, design-doc, plan-reviewer, apply-plan-review,
# run-plan, run-review, next-step-taker). The stronghold (~/code, origin
# OWNER/stronghold) tracks workflow/skills/plans — not app source — and pushes
# DIRECT to main with no PR/review gate (see ~/code/CLAUDE.md "Git Workflow"
# and "Stronghold Auto-Sync").
#
# Behavior:
#   * Path-scoped staging ONLY — plans/ changelog/ .claude/ CLAUDE.md
#     .gitignore repos.txt misc/ Makefile and root-level *.md — never a blanket
#     `git add -A`, so a stray scratch/sub-repo edit never rides along.
#     (Sub-repos are gitignored anyway.) The always-commit files
#     (.claude/**, CLAUDE.md, .gitignore) are included by construction.
#   * Skips cleanly (exit 0, no commit) when nothing under those paths changed —
#     safe to call unconditionally at the end of a skill even if it wrote nothing.
#   * Commits with the message passed as $1 (or a generic timestamped default),
#     then `git push origin main`.
#   * SAFETY GUARD: refuses to run unless origin is the stronghold
#     (OWNER/stronghold) AND the current branch is the default branch — so it can
#     never fire inside a sub-repo working tree or on a feature branch.
#
# Usage:
#   stronghold-sync.sh "plans(app-main): plan-reviewer login-enumeration-hardening"
#   stronghold-sync.sh          # generic timestamped message
#
# Keep the message free of shell metacharacters `;` `&&` `||` `$(` and backticks:
# the PreToolUse compound-command / command-substitution guard hooks inspect the
# whole invocation string and will BLOCK the call if the message contains them
# (a `;`+space or `&&`/`||` reads as a command chain). Plain parens are fine
# (that's why the `plans(<repo>):` prefix is safe). Kebab-case plan names never
# trip this; only hand-written prose messages need the care.
#
# SSH push auth requires an UNSANDBOXED run — invoke via Bash with
# dangerouslyDisableSandbox: true (origin is git@github.com:OWNER/stronghold.git;
# see ~/code/CLAUDE.md Git Workflow).
set -euo pipefail

# Resolve the stronghold root from this script's own location, independent of cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STRONGHOLD="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$STRONGHOLD"

# --- safety guard: only the stronghold, only on its default branch -----------
origin_url="$(git remote get-url origin 2>/dev/null || true)"
if ! printf '%s' "$origin_url" | grep -qiE '[:/]OWNER/stronghold(\.git)?/?$'; then
  echo "stronghold-sync: origin is not OWNER/stronghold ($origin_url) — refusing to run." >&2
  exit 1
fi
branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '')"
if [ "$branch" != "main" ]; then
  echo "stronghold-sync: on branch '$branch', not main — refusing to auto-push. Commit/push manually." >&2
  exit 1
fi

# --- commit message ----------------------------------------------------------
msg="${1:-}"
if [ -z "$msg" ]; then
  ts="$(date '+%m-%d-%Y %H:%M')"
  msg="chore(stronghold): auto-sync plan/review/changelog ($ts)"
fi

# --- path-scoped staging (never a blanket add) -------------------------------
# Stage new/modified/deleted within the workflow surface only. Absent paths are
# ignored; the `|| true` keeps a missing optional path (e.g. misc/) non-fatal.
for p in plans changelog .claude CLAUDE.md .gitignore repos.txt misc Makefile; do
  [ -e "$p" ] && git add -- "$p" 2>/dev/null || true
done
# Root-level markdown (README etc.) without recursing into subdirs.
for f in ./*.md; do
  [ -e "$f" ] && git add -- "$f"
done

# --- nothing staged? skip cleanly --------------------------------------------
if git diff --cached --quiet; then
  echo "stronghold-sync: no stronghold changes to commit — nothing to do."
  exit 0
fi

# --- hygiene lint on the staged diff (secrets, mock PNGs, ...) ---------------
if ! "$SCRIPT_DIR/workflow-lint.sh" --group hygiene --staged >&2; then
  echo "stronghold-sync: workflow-lint hygiene errors in staged changes, refusing to commit." >&2
  exit 1
fi

# --- commit + push to main ---------------------------------------------------
git commit -m "$msg" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
git push origin main
echo "stronghold-sync: committed + pushed to main — $msg"
