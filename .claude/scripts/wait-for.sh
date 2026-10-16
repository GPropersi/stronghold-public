#!/usr/bin/env bash
#
# wait-for.sh — block, in ONE call, until a condition holds or a timeout expires.
#
# An agent with nothing to do but wait (an external log to appear, a background process to exit)
# must not burn a model turn per poll: every turn re-reads the whole context. One bounded wait
# replaces dozens of `echo waiting-N` no-op calls.
#
# Usage:
#   wait-for.sh [--timeout <S>] [--interval <S>] [--file <path>]
#               [--contains <regex> --in <path>] [--pid <pid>]
#
#   (no condition)          just sleep for --timeout seconds
#   --file <path>           satisfied once the file exists and is non-empty
#   --contains R --in <p>   satisfied once the file <p> contains a line matching extended regex R
#   --pid <pid>             satisfied once that process has exited
#   --timeout <S>           give up after S seconds (default 60; clamped to WAIT_FOR_MAX, default 1700)
#   --interval <S>          seconds between checks (default 5; fractions allowed)
#
# Several conditions must ALL hold. Do not use this to wait for a foreground subagent: its Agent-tool
# reply is the completion signal. Do not poll a result file of an agent that is still running.
#
# Prints one line, `wait-for: satisfied after <N>s` or `wait-for: timed out after <N>s`.
# Env: WAIT_FOR_MAX (timeout clamp, seconds)
# Exit: 0 satisfied (or plain sleep finished) | 124 timed out | 2 usage error
set -euo pipefail

die() {
  echo "wait-for.sh: $1" >&2
  exit 2
}

timeout=60
interval=5
file=""
contains=""
in_path=""
pid=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h | --help)
      sed -n '2,/^set /{/^set /!p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    --timeout | --interval | --file | --contains | --in | --pid)
      [ "$#" -ge 2 ] || die "$1 needs a value"
      case "$1" in
        --timeout) timeout="$2" ;;
        --interval) interval="$2" ;;
        --file) file="$2" ;;
        --contains) contains="$2" ;;
        --in) in_path="$2" ;;
        --pid) pid="$2" ;;
      esac
      shift 2
      ;;
    *) die "unknown argument '$1'" ;;
  esac
done

case "$timeout" in '' | *[!0-9]*) die "--timeout must be a whole number of seconds" ;; esac
printf '%s' "$interval" | grep -Eq '^[0-9]+(\.[0-9]+)?$' || die "--interval must be a number"
[ -z "$pid" ] || case "$pid" in *[!0-9]*) die "--pid must be a number" ;; esac
{ [ -n "$contains" ] && [ -z "$in_path" ]; } && die "--contains needs --in <path>"
{ [ -z "$contains" ] && [ -n "$in_path" ]; } && die "--in needs --contains <regex>"

max="${WAIT_FOR_MAX:-1700}"
if [ "$timeout" -gt "$max" ]; then
  echo "wait-for: --timeout $timeout clamped to $max" >&2
  timeout="$max"
fi

if [ -z "$file" ] && [ -z "$contains" ] && [ -z "$pid" ]; then
  sleep "$timeout"
  echo "wait-for: slept ${timeout}s"
  exit 0
fi

satisfied() {
  if [ -n "$file" ] && [ ! -s "$file" ]; then return 1; fi
  if [ -n "$contains" ] && ! { [ -f "$in_path" ] && grep -Eq -- "$contains" "$in_path"; }; then return 1; fi
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then return 1; fi
  return 0
}

start="$(date +%s)"
while :; do
  elapsed=$(($(date +%s) - start))
  if satisfied; then
    echo "wait-for: satisfied after ${elapsed}s"
    exit 0
  fi
  if [ "$elapsed" -ge "$timeout" ]; then
    echo "wait-for: timed out after ${elapsed}s"
    exit 124
  fi
  sleep "$interval"
done
