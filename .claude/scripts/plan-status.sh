#!/usr/bin/env bash
#
# plan-status.sh — outline a plan, show checkbox progress per step, and print just one step's text.
#
# Replaces the grep -n "^### " / grep -c "^- \[x\]" / "read the whole plan to find step N" sequence
# with one call, and lets an executor read only its own step instead of the full plan.
#
# Usage:
#   plan-status.sh <plan>                         outline: frontmatter, progress, per-step line ranges
#   plan-status.sh <plan> --step N                print step N's text (its `### N.` heading to the next step)
#   plan-status.sh <plan> --step next             print the first step that still has an unchecked box
#   plan-status.sh <plan> --step N --with-context ... preceded by everything before the first step
#                                                 (Summary, Research Findings, shared reference sections)
#   plan-status.sh <plan> --verify                exit 0 when every unmarked box is ticked; else print one
#                                                 `unticked: step N: <text>` line per open box, exit 5
#
#   <plan>  a path to the .md file, <repo>/<plan-name>, or a bare <plan-name>; names are looked up as
#           $PLANS_DIR/<repo>/{open,completed}/<topic>/<plan-name>.md (open/ wins over completed/)
#
# A step is a `### N.` heading (also `### Step N:`) inside the plan; checkboxes are `- [ ]` / `- [x]`
# anywhere in the step's range. The outline also lists the plan's non-step sections with their line
# ranges, so a section named like "reference for every step" can be read with sed -n 'a,bp'.
#
# Held items: `- [~]` (deferred) and an unticked `- [ ] (post-merge)` / `(manual, post-merge)` box are
# excluded from done/total and from --verify; the outline reports them as `held: P post-merge, D deferred`
# and a `+N held` suffix on the step row. A ticked tagged box is plain done. Fenced code blocks and the
# `## Progress Tracking` section are never scanned or counted. --verify prints a `note:` and exits 0 for a
# `legacy: true` plan or one with no parseable steps; it cannot be combined with --step/--with-context.
#
# Env (test seam): PLANS_DIR (default <stronghold>/plans)
# Exit: 0 ok | 2 usage error | 3 ambiguous plan name | 4 plan or step not found | 5 --verify found open boxes
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STRONGHOLD="$(cd "$SCRIPT_DIR/../.." && pwd)"
PLANS_DIR="${PLANS_DIR:-$STRONGHOLD/plans}"

die() {
  echo "plan-status.sh: $1" >&2
  exit "${2:-2}"
}

plan_arg=""
step=""
with_context=0
verify=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h | --help)
      sed -n '2,/^set /{/^set /!p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    --step)
      [ "$#" -ge 2 ] || die "--step needs N or 'next'"
      step="$2"
      shift 2
      ;;
    --with-context)
      with_context=1
      shift
      ;;
    --verify)
      verify=1
      shift
      ;;
    -*) die "unknown option '$1'" ;;
    *)
      [ -z "$plan_arg" ] || die "unexpected argument '$1'"
      plan_arg="$1"
      shift
      ;;
  esac
done
[ -n "$plan_arg" ] || die "usage: plan-status.sh <plan> [--step N|next] [--with-context] | --verify"
if [ "$verify" -eq 1 ] && { [ -n "$step" ] || [ "$with_context" -eq 1 ]; }; then
  die "--verify cannot be combined with --step or --with-context (usage: plan-status.sh <plan> --verify)"
fi
if [ -n "$step" ] && [ "$step" != "next" ]; then
  case "$step" in '' | *[!0-9]*) die "--step must be a number or 'next'" ;; esac
fi

resolve_plan() {
  local arg="${1%.md}" repo="" name="" state matches
  if [ -f "$1" ]; then
    printf '%s\n' "$1"
    return 0
  fi
  if [ -f "$arg.md" ]; then
    printf '%s\n' "$arg.md"
    return 0
  fi
  case "$arg" in
    */*)
      repo="${arg%%/*}"
      name="${arg#*/}"
      ;;
    *) name="$arg" ;;
  esac
  case "$name" in */*) die "cannot resolve plan '$1' (use <repo>/<plan-name> or a file path)" 4 ;; esac
  for state in open completed; do
    if [ -n "$repo" ]; then
      matches="$(find "$PLANS_DIR/$repo/$state" -mindepth 2 -maxdepth 2 -name "$name.md" 2>/dev/null | sort || true)"
    else
      matches="$(find "$PLANS_DIR" -mindepth 4 -maxdepth 4 -path "*/$state/*" -name "$name.md" 2>/dev/null | sort || true)"
    fi
    if [ -n "$matches" ]; then
      if [ "$(printf '%s\n' "$matches" | wc -l | tr -d ' ')" -gt 1 ]; then
        echo "plan-status.sh: ambiguous plan name '$1', candidates:" >&2
        printf '%s\n' "$matches" | sed 's/^/  /' >&2
        exit 3
      fi
      printf '%s\n' "$matches"
      return 0
    fi
  done
  die "plan not found: $1 (looked under $PLANS_DIR/<repo>/{open,completed}/<topic>/)" 4
}

plan="$(resolve_plan "$plan_arg")"
total_lines="$(wc -l <"$plan" | tr -d ' ')"

# One awk pass: frontmatter, step table, non-step sections, checkbox counts.
# Emits tab-separated records: FM key value | STEP n start end done total title held | SEC start end title
#   | OPEN label text | TOT done total | HELD post-merge deferred
# Fenced code blocks and the `## Progress Tracking` section are skipped for headings, boxes and finished:.
parsed="$(
  awk '
    function flush_step() {
      if (cur_n != "") {
        printf "STEP\t%s\t%d\t%d\t%d\t%d\t%s\t%d\n", cur_n, cur_start, NR_end, cur_done, cur_total, cur_title, cur_held
      }
    }
    function flush_sec() {
      if (sec_title != "") { printf "SEC\t%d\t%d\t%s\n", sec_start, NR_end, sec_title }
    }
    BEGIN { infm = 0; fmdone = 0; cur_n = ""; sec_title = ""; fence = ""; in_pt = 0; held_pm = 0; held_df = 0 }
    NR == 1 && $0 == "---" { infm = 1; next }
    infm == 1 {
      if ($0 == "---") { infm = 0; fmdone = 1; next }
      if (match($0, /^[A-Za-z_]+:/)) {
        key = substr($0, 1, RLENGTH - 1); val = substr($0, RLENGTH + 1); sub(/^[ \t]+/, "", val)
        printf "FM\t%s\t%s\n", key, val
      }
      next
    }
    match($0, /^[ \t]*([`][`][`]+|[~][~][~]+)/) {
      f = substr($0, 1, RLENGTH); sub(/^[ \t]+/, "", f)
      if (fence == "") { fence = f }
      else if (substr(f, 1, 1) == substr(fence, 1, 1) && length(f) >= length(fence)) { fence = "" }
      next
    }
    fence != "" { next }
    /^finished:/ { val = $0; sub(/^finished:[ \t]*/, "", val); printf "FM\tfinished\t%s\n", val }
    {
      is_h2 = ($0 ~ /^##[ \t]/)
      if (is_h2) {
        t = $0; sub(/^##[ \t]+/, "", t); sub(/[ \t]+$/, "", t)
        in_pt = (tolower(t) == "progress tracking")
      }
      is_step = (!in_pt && $0 ~ /^###[ \t]+(Step[ \t]+)?[0-9]+[.:)][ \t]/)
      if (is_step || is_h2) {
        NR_end = NR - 1
        flush_step(); cur_n = ""
        if (is_h2 || is_step) { flush_sec(); sec_title = "" }
        if (is_step) {
          t = $0; sub(/^###[ \t]+(Step[ \t]+)?/, "", t)
          cur_n = t; sub(/[.:)].*$/, "", cur_n)
          sub(/^[0-9]+[.:)][ \t]+/, "", t)
          cur_title = t; cur_start = NR; cur_done = 0; cur_total = 0; cur_held = 0
        } else {
          sec_title = $0; sub(/^##[ \t]+/, "", sec_title); sec_start = NR
        }
      }
      if (!in_pt && match($0, /^[ \t]*- \[[ xX~]\]/)) {
        marker = substr($0, RLENGTH - 1, 1)
        text = substr($0, RLENGTH + 1); sub(/^[ \t]+/, "", text)
        if (marker == "~") {
          held_df++
          if (cur_n != "") cur_held++
        } else if (marker == " " && (text ~ /^\(post-merge\)/ || text ~ /^\(manual,[ \t]*post-merge\)/)) {
          held_pm++
          if (cur_n != "") cur_held++
        } else {
          if (cur_n != "") {
            cur_total++
            if (marker != " ") cur_done++
          }
          all_total++
          if (marker != " ") all_done++
          else printf "OPEN\t%s\t%s\n", (cur_n != "" ? "step " cur_n : "(no step)"), text
        }
      }
    }
    END {
      NR_end = NR
      flush_step(); flush_sec()
      printf "TOT\t%d\t%d\n", all_done + 0, all_total + 0
      printf "HELD\t%d\t%d\n", held_pm + 0, held_df + 0
    }
  ' "$plan"
)"

fm() { printf '%s\n' "$parsed" | awk -F'\t' -v k="$1" '$1 == "FM" && $2 == k {v = $3} END {print v}'; }

# The last step runs only to the end of its section: stop at the next `## ` heading.
# (awk flushes a step at the next heading, so ranges already end before it.)
step_rows="$(printf '%s\n' "$parsed" | awk -F'\t' '$1 == "STEP"')"

if [ "$verify" -eq 1 ]; then
  legacy="$(fm legacy | tr -d "'\" \t" | tr '[:upper:]' '[:lower:]')"
  if [ "$legacy" = "true" ]; then
    echo "note: legacy plan (legacy: true), step checks skipped"
    exit 0
  fi
  if [ -z "$step_rows" ]; then
    echo "note: no parseable steps in $plan, nothing to verify"
    exit 0
  fi
  open_rows="$(printf '%s\n' "$parsed" | awk -F'\t' '$1 == "OPEN" {print "unticked: " $2 ": " $3}')"
  if [ -n "$open_rows" ]; then
    printf '%s\n' "$open_rows"
    exit 5
  fi
  echo "verify: ok"
  exit 0
fi

if [ -n "$step" ]; then
  [ -n "$step_rows" ] || die "no steps found in $plan" 4
  if [ "$step" = "next" ]; then
    row="$(printf '%s\n' "$step_rows" | awk -F'\t' '$6 > $5 {print; exit}')"
    [ -n "$row" ] || die "no step with an unchecked box in $plan (all steps complete)" 4
  else
    row="$(printf '%s\n' "$step_rows" | awk -F'\t' -v n="$step" '$2 == n {print; exit}')"
    [ -n "$row" ] || die "step $step not found in $plan" 4
  fi
  s_n="$(printf '%s' "$row" | cut -f2)"
  s_start="$(printf '%s' "$row" | cut -f3)"
  s_end="$(printf '%s' "$row" | cut -f4)"
  if [ "$with_context" -eq 1 ]; then
    first_start="$(printf '%s\n' "$step_rows" | awk -F'\t' 'NR == 1 {print $3}')"
    # Everything before the first step (skip the "## Steps" heading line itself if it is the last line).
    ctx_end=$((first_start - 1))
    echo "# context: lines 1-$ctx_end of $plan"
    sed -n "1,${ctx_end}p" "$plan"
    echo
  fi
  echo "# step $s_n: lines $s_start-$s_end of $plan"
  sed -n "${s_start},${s_end}p" "$plan"
  exit 0
fi

# Outline
echo "plan: $plan ($total_lines lines)"
fm_line="repo: $(fm repo)  branch: $(fm branch)  worktree: $(fm worktree)  finished: $(fm finished)"
issue="$(fm github_issue)"
[ -z "$issue" ] || fm_line="$fm_line  issue: $issue"
echo "$fm_line"

tot_done="$(printf '%s\n' "$parsed" | awk -F'\t' '$1 == "TOT" {print $2}')"
tot_all="$(printf '%s\n' "$parsed" | awk -F'\t' '$1 == "TOT" {print $3}')"
held_pm="$(printf '%s\n' "$parsed" | awk -F'\t' '$1 == "HELD" {print $2}')"
held_df="$(printf '%s\n' "$parsed" | awk -F'\t' '$1 == "HELD" {print $3}')"
n_steps="$(printf '%s\n' "$step_rows" | grep -c . || true)"
next_n="$(printf '%s\n' "$step_rows" | awk -F'\t' '$6 > $5 {print $2; exit}')"
echo "progress: $tot_done/$tot_all checked, $n_steps steps, next incomplete: ${next_n:-none}"
if [ $((held_pm + held_df)) -gt 0 ]; then
  echo "held: $held_pm post-merge, $held_df deferred"
fi

if [ -n "$step_rows" ]; then
  echo "steps (# lines done title):"
  printf '%s\n' "$step_rows" | awk -F'\t' '{printf "  %-3s %-9s %-7s %s%s\n", $2, $3 "-" $4, $5 "/" $6, $7, ($8 > 0 ? " +" $8 " held" : "")}'
fi
sections="$(printf '%s\n' "$parsed" | awk -F'\t' '$1 == "SEC" {printf "  %-9s %s\n", $2 "-" $3, $4}')"
if [ -n "$sections" ]; then
  echo "sections (lines title):"
  printf '%s\n' "$sections"
fi
