#!/usr/bin/env bash
#
# changelog-add.sh — append one entry to today's stronghold changelog in a single call.
#
# Replaces the hand-run ritual (ls the file, `date`, tail it, Edit/echo >>) with one command that
# computes the REAL local date+time, creates `changelog/MM-DD-YYYY-changelog.md` with its
# `# Changelog — MM/DD/YYYY` header when missing, and appends `- [HH:MM] <entry>`.
#
# Usage:
#   changelog-add.sh "<action>: <1-2 line summary>"
#   changelog-add.sh "<action>: <summary>" --sub "<detail line>" [--sub "<detail line>" ...]
#   changelog-add.sh -          # entry read from stdin; extra lines become indented continuation lines
#
# Each --sub becomes an indented `  - <detail>` line under the entry. Several plain arguments are
# joined with spaces into the one-line entry.
#
# Env (test seams):
#   CHANGELOG_DIR   directory holding the changelog files (default: <stronghold>/changelog)
#   CHANGELOG_NOW   fixed "MM-DD-YYYY HH:MM" instead of the current time
#
# Prints one line: `changelog-add: <file> [HH:MM]`.
# Exit: 0 ok | 2 usage error (empty entry, bad flag, malformed CHANGELOG_NOW)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STRONGHOLD="$(cd "$SCRIPT_DIR/../.." && pwd)"
DIR="${CHANGELOG_DIR:-$STRONGHOLD/changelog}"

usage_error() {
  echo "changelog-add.sh: $1" >&2
  exit 2
}

entry_parts=()
subs=()
from_stdin=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h | --help)
      sed -n '2,/^set /{/^set /!p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    --sub)
      [ "$#" -ge 2 ] || usage_error "--sub needs a value"
      subs+=("$2")
      shift 2
      ;;
    -)
      from_stdin=1
      shift
      ;;
    -*) usage_error "unknown option '$1'" ;;
    *)
      entry_parts+=("$1")
      shift
      ;;
  esac
done

if [ "$from_stdin" -eq 1 ]; then
  entry="$(cat)"
else
  entry="${entry_parts[*]:-}"
fi
# Trim leading/trailing whitespace; an empty entry is a usage error.
entry="$(printf '%s' "$entry" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
[ -n "$entry" ] || usage_error "empty entry (usage: changelog-add.sh \"<action>: <summary>\")"

if [ -n "${CHANGELOG_NOW:-}" ]; then
  now="$CHANGELOG_NOW"
else
  now="$(date '+%m-%d-%Y %H:%M')"
fi
case "$now" in
  [0-9][0-9]-[0-9][0-9]-[0-9][0-9][0-9][0-9]\ [0-9][0-9]:[0-9][0-9]) ;;
  *) usage_error "CHANGELOG_NOW must look like 'MM-DD-YYYY HH:MM' (got '$now')" ;;
esac
day="${now%% *}"
hhmm="${now##* }"
file="$DIR/$day-changelog.md"

mkdir -p "$DIR"
if [ ! -f "$file" ]; then
  printf '# Changelog — %s\n\n' "${day//-//}" >"$file"
fi
# Make sure the previous line is terminated before appending.
if [ -s "$file" ] && [ "$(tail -c 1 "$file" | wc -l)" -eq 0 ]; then
  printf '\n' >>"$file"
fi

{
  first=1
  while IFS= read -r line; do
    if [ "$first" -eq 1 ]; then
      printf -- '- [%s] %s\n' "$hhmm" "$line"
      first=0
    else
      case "$line" in
        [[:space:]]*) printf '%s\n' "$line" ;;
        *) printf '  %s\n' "$line" ;;
      esac
    fi
  done <<<"$entry"
  for sub in ${subs[@]+"${subs[@]}"}; do
    printf '  - %s\n' "$sub"
  done
} >>"$file"

echo "changelog-add: $file [$hhmm]"
