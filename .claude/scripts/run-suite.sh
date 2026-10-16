#!/usr/bin/env bash
#
# run-suite.sh — run one verification `make` target in a sub-repo checkout and print a COMPACT
# result, so an orchestrator needs no test-runner subagent and no tail/grep/rm follow-up calls.
#
# Usage:
#   run-suite.sh [--expect-branch <branch>] [--keep] [--tail <N>] <dir> <make-target> [VAR=value ...]
#
#   <dir>           a checkout under the stronghold root (e.g. ~/code/app-main or one of its
#                   .claude/worktrees/<slug>); must contain a Makefile
#   <make-target>   test-*, lint*, format-check, typecheck, vite-build*, audit* (anything else is refused)
#   VAR=value       make variables only (m=unit, n=4, f=tests/x.py, args=...); flags are refused
#   --expect-branch exit 3 BEFORE running when `git -C <dir>` is not on that branch (worktree discipline)
#   --keep          keep the full output file even when the run passes (default: kept only on failure)
#   --tail <N>      lines of output tail to print (default 12)
#
# The full output goes to $RUN_SUITE_OUT_DIR (default /tmp/claude)/suite-<slug>-<target>-<epoch>-<pid>.txt.
# The script prints: the command, PASS/FAIL + exit code + duration, the pytest/vitest/unittest summary
# lines, up to 20 distinct FAILED/ERROR test ids, make's own error lines, the output path and the tail.
# Read the output file only for the failures named here. The exit code is make's.
#
# Runs unsandboxed (declared in sandbox.excludedCommands) so docker-backed targets work; the target
# allowlist, the VAR=value-only argument rule and the stronghold-root jail keep that narrow.
#
# Env (test seams): RUN_SUITE_OUT_DIR (output dir), RUN_SUITE_ROOT (jail root, default: the stronghold)
# Exit: make's exit code | 2 usage/refused | 3 branch mismatch
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STRONGHOLD="$(cd "$SCRIPT_DIR/../.." && pwd)"
ROOT="${RUN_SUITE_ROOT:-$STRONGHOLD}"
OUT_DIR="${RUN_SUITE_OUT_DIR:-/tmp/claude}"

die() {
  echo "run-suite.sh: $1" >&2
  exit "${2:-2}"
}

expect_branch=""
keep=0
tail_n=12
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h | --help)
      sed -n '2,/^set /{/^set /!p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    --expect-branch)
      [ "$#" -ge 2 ] || die "--expect-branch needs a value"
      expect_branch="$2"
      shift 2
      ;;
    --keep)
      keep=1
      shift
      ;;
    --tail)
      [ "$#" -ge 2 ] || die "--tail needs a value"
      tail_n="$2"
      shift 2
      ;;
    --) shift; break ;;
    -*) die "unknown option '$1'" ;;
    *) break ;;
  esac
done

[ "$#" -ge 2 ] || die "usage: run-suite.sh [--expect-branch B] [--keep] [--tail N] <dir> <make-target> [VAR=value ...]"
case "$tail_n" in '' | *[!0-9]*) die "--tail must be a number" ;; esac

dir_in="$1"
target="$2"
shift 2

# Fail closed: only these verification targets may run through this unsandboxed wrapper.
if ! printf '%s' "$target" | grep -Eq '^(test-[A-Za-z0-9_-]+|lint[A-Za-z0-9_-]*|format-check|typecheck|vite-build[A-Za-z0-9_-]*|audit[A-Za-z0-9_-]*)$'; then
  die "refusing make target '$target' (allowed: test-*, lint*, format-check, typecheck, vite-build*, audit*)"
fi
for arg in "$@"; do
  printf '%s' "$arg" | grep -Eq '^[A-Za-z_][A-Za-z0-9_]*=' || die "refusing argument '$arg' (only VAR=value make variables are allowed)"
done

dir="$(cd "$dir_in" 2>/dev/null && pwd -P)" || die "no such directory: $dir_in"
root_real="$(cd "$ROOT" && pwd -P)"
case "$dir/" in
  "$root_real"/*) ;;
  *) die "refusing $dir (must be inside $root_real)" ;;
esac
[ -f "$dir/Makefile" ] || die "no Makefile in $dir"

if [ -n "$expect_branch" ]; then
  actual="$(git -C "$dir" branch --show-current 2>/dev/null || true)"
  if [ "$actual" != "$expect_branch" ]; then
    echo "run-suite.sh: branch mismatch in $dir: expected '$expect_branch', found '${actual:-<detached or none>}'. Not running; do not check out or switch branches." >&2
    exit 3
  fi
fi

mkdir -p "$OUT_DIR"
slug="$(basename "$dir" | tr -c 'A-Za-z0-9\n._-' '-')"
out="$OUT_DIR/suite-$slug-$target-$(date +%s)-$$.txt"

start="$(date +%s)"
make -C "$dir" "$target" "$@" >"$out" 2>&1
rc=$?
secs=$(($(date +%s) - start))

lines="$(wc -l <"$out" | tr -d ' ')"
if [ "$rc" -eq 0 ]; then verdict="PASS"; else verdict="FAIL"; fi

echo "run-suite: make -C $dir $target $*"
echo "result: $verdict (exit $rc) in ${secs}s"

summary=""
add_summary() { [ -n "$1" ] && summary="${summary}  $1"$'\n'; }
add_summary "$(grep -E '^=+ .*(passed|failed|error).* in [0-9.]+s' "$out" | tail -n 1)"
add_summary "$(grep -E '^ *Test Files +[0-9]' "$out" | tail -n 1 | sed 's/^ *//')"
add_summary "$(grep -E '^ *Tests +[0-9]' "$out" | tail -n 1 | sed 's/^ *//')"
add_summary "$(grep -E '^Ran [0-9]+ tests? in ' "$out" | tail -n 1)"
add_summary "$(grep -E '^(OK|FAILED) ?(\(.*\))?$' "$out" | tail -n 1)"
if [ -n "$summary" ]; then
  echo "summary:"
  printf '%s' "$summary"
fi

failed="$(grep -E '^(FAILED|ERROR) ' "$out" | sort -u)"
if [ -n "$failed" ]; then
  total="$(printf '%s\n' "$failed" | wc -l | tr -d ' ')"
  echo "failures ($total distinct, first 20):"
  printf '%s\n' "$failed" | head -n 20 | sed 's/^/  /'
fi
merr="$(grep -E '^make(\[[0-9]+\])?: \*\*\*' "$out" | tail -n 3)"
if [ -n "$merr" ]; then
  echo "make errors:"
  printf '%s\n' "$merr" | sed 's/^/  /'
fi

if [ "$rc" -eq 0 ] && [ "$keep" -eq 0 ]; then
  rm -f "$out"
  echo "output: removed ($lines lines, run passed; use --keep to retain)"
else
  echo "output: $out ($lines lines)"
  echo "--- last $tail_n lines ---"
  tail -n "$tail_n" "$out"
fi
exit "$rc"
