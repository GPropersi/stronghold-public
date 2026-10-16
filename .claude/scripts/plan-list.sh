#!/usr/bin/env bash
#
# plan-list.sh — enumerate plans in the CENTRAL stronghold store, grouped by
# repo and lifecycle state (open/completed).
#
# Store layout (see ~/code/CLAUDE.md "Central Plans Store"):
#   ~/code/plans/<repo>/open/<topic>/<name>.md
#   ~/code/plans/<repo>/completed/<topic>/<name>.md
#   ~/code/plans/<repo>/research/        (standalone research — NOT listed here)
#
# Usage:
#   plan-list.sh                 every repo, grouped by repo then Open/Completed
#   plan-list.sh <repo>          only that repo's bucket (e.g. app-main)
#   plan-list.sh --open [<repo>] only open plans (optionally scoped to one repo)
#
# Status is derived from the FOLDER (completed/ = done, open/ = open) — the
# primary signal. The `## Status` `finished:` value is read only as a secondary
# check: a completed/ plan marked `finished: false` (or vice-versa) is flagged
# with a mismatch warning so drift is visible.
#
# Master vs sub: filename ending in `-master.md`. Related GitHub issue: the
# `github_issue:` YAML frontmatter line. Only topic-root `.md` files count;
# reviews/, research/, mocks/, screenshots/, tmp/ subfolders are excluded by depth.
#
# One awk pass emits a TSV row per plan; no file contents leave the script.
set -euo pipefail

# Resolve the stronghold plans dir from this script's own location
# (.claude/scripts/ -> repo root -> plans/), independent of cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STRONGHOLD="$(cd "$SCRIPT_DIR/../.." && pwd)"
PLANS_DIR="${CLAUDE_PLANS_DIR:-$STRONGHOLD/plans}"

if [ ! -d "$PLANS_DIR" ]; then
  echo "No plans/ store found at $PLANS_DIR"
  exit 0
fi

# --- args ---
open_only=0
repo_filter=""
for arg in "$@"; do
  case "$arg" in
    --open) open_only=1 ;;
    -h|--help)
      sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    --*) echo "plan-list.sh: unknown option '$arg'" >&2; exit 2 ;;
    *) repo_filter="$arg" ;;
  esac
done

# --- collect + emit one TSV row per plan (topic-root .md under open|completed) ---
# depth 3 = <repo>/<state>/<name>.md ; depth 4 = <repo>/<state>/<topic>/<name>.md
# `find -exec awk {} +` batches every file into one awk run and simply does not
# run awk when nothing matches (no temp file / no xargs-empty edge case).
rows="$(find "$PLANS_DIR" -mindepth 3 -maxdepth 4 -type f -name '*.md' \
  \( -path '*/open/*' -o -path '*/completed/*' \) \
  -exec awk -v plans="$PLANS_DIR" -v repofilter="$repo_filter" -v openonly="$open_only" '
    function emit(   stateord, mism) {
      if (!seen) return
      if (repofilter != "" && repo != repofilter) return
      if (openonly == 1 && state != "open") return
      stateord = (state == "open") ? 0 : 1
      # Only the confusing direction is real drift: a plan filed under completed/
      # but still marked `finished: false`. The reverse (a finished sub-plan sitting
      # in open/ because its master topic is not fully done) is expected under the
      # topic-as-unit lifecycle, so it is NOT flagged.
      mism = (state == "completed" && finished == "false") ? 1 : 0
      printf "%s\t%d\t%s\t%d\t%s\t%s\t%s\t%s\t%d\n", \
        repo, stateord, state, master, topic, name, issue, finished, mism
    }
    FNR == 1 {
      emit()
      seen = 1
      rel = FILENAME; sub("^" plans "/", "", rel)
      n = split(rel, c, "/")
      repo = c[1]; state = c[2]
      if (n >= 4) { topic = c[3] } else { topic = "(root)" }
      name = c[n]; sub(/\.md$/, "", name)
      master = (name ~ /-master$/) ? 1 : 0
      issue = ""; finished = "NONE"; instatus = 0
    }
    /^github_issue:[[:space:]]*/ { if (issue == "") issue = $2 }
    /^##[[:space:]]+Status/ { instatus = 1 }
    instatus == 1 && /^finished:[[:space:]]*/ && finished == "NONE" { finished = $2 }
    END { emit() }
  ' {} + | sort -t"$(printf '\t')" -k1,1 -k2,2n -k5,5 -k4,4n -k6,6)"

if [ -z "$rows" ]; then
  scope="${repo_filter:+ for '$repo_filter'}"
  echo "No plans found in the store$scope."
  exit 0
fi

# --- stage 2: format grouped by repo -> state ---
title="# Plans"
[ -n "$repo_filter" ] && title="# Plans — $repo_filter"
[ "$open_only" -eq 1 ] && title="$title (open only)"
echo "$title"

printf '%s\n' "$rows" | awk -F'\t' '
  {
    repo=$1; state=$3; master=$4; topic=$5; name=$6; issue=$7; finished=$8; mism=$9
    if (repo != crepo) { crepo=repo; cstate=""; printf "\n## %s\n", repo }
    if (state != cstate) { cstate=state; printf "### %s\n", (state=="open" ? "Open" : "Completed") }
    box = (state=="completed") ? "x" : " "
    mt = (master==1) ? "  (master)" : ""
    it = (issue=="") ? "  (no issue)" : "  #" issue
    mm = (mism==1) ? "  [!] folder/finished mismatch (finished: " finished ")" : ""
    printf "  - [%s] %s/%s%s%s%s\n", box, topic, name, mt, it, mm
    total++
    if (state=="open") open++; else done++
    if (issue!="") linked++
    if (mism==1) { mismn++; misslist[mismn]=repo "/" state "/" topic "/" name " (finished: " finished ")" }
    if (state=="open") { notdone++; ndlist[notdone]=sprintf("  - [ ] %s/%s%s%s  · %s", topic, name, mt, it, repo) }
  }
  END {
    printf "\nTotal: %d plans — %d completed, %d open · %d/%d linked to a GitHub issue\n", \
      total, done+0, open+0, linked+0, total
    print "Legend: [x] completed (in completed/)  [ ] open (in open/)  ·  #N = GitHub issue  ·  [!] = folder vs finished: drift"
    if (mismn > 0) {
      printf "\n## Mismatches (%d) — folder says one thing, `finished:` says another\n", mismn
      for (i=1; i<=mismn; i++) print "  - " misslist[i]
    }
    if (notdone > 0) {
      printf "\n## Not Done (%d)\n", notdone
      for (i=1; i<=notdone; i++) print ndlist[i]
    }
  }
'
