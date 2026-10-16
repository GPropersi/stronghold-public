#!/usr/bin/env bash
#
# playwright-reap-idle.sh
#
# Closes a Playwright-MCP browser that nobody has driven for a while, so a
# session that forgot browser_close cannot leak a headless Chromium.
#
# WHY THIS EXISTS
# @playwright/mcp has NO idle or session timeout — its only timeout flags are
# per-operation (--timeout-action / --timeout-navigation / --cdp-timeout). An
# un-closed browser therefore lives until the Claude session process itself
# exits, and a session parked at a question prompt keeps it alive indefinitely.
# On 2026-09-18 one held 1,064 MB across 8 processes for 10 hours while waiting
# on an AskUserQuestion. plan-creator's UI Mockup Protocol now mandates a
# browser_close, but that is an instruction to a model, not an enforcement.
# This is the enforcement.
#
# HOW "IDLE" IS MEASURED — by the MCP's own activity record, not by CPU
# playwright-mcp writes an artifact into <cwd>/.playwright-mcp per tool call
# (page-<ts>.yml snapshots, console-<ts>.log, saved screenshots). The newest
# mtime in that directory is therefore a DIRECT record of when a tool last
# drove the browser. If that is older than the threshold, nobody is using it.
#
# CPU time was tried first and rejected, for two measured reasons:
#   * too coarse the portable way — a real page load moved the browser only
#     ~9 jiffies (90 ms), which `ps -o time=` (whole seconds) cannot see at all;
#   * too noisy the precise way — sampling utime+stime from /proc showed the
#     browser accumulating CPU continuously, so an exact-equality test never
#     converges and no threshold cleanly separates "in use" from "sitting there".
# Wall-clock age was rejected outright: a browser mid-workflow is idle between
# turns, so age alone reaps browsers that are still in active use.
#
# The kill is delegated to playwright-unlock.sh — one hardened kill path
# (graceful SIGTERM, escalate to SIGKILL, then clear stale Singleton* locks)
# rather than a second copy of it here. Closing is cheap: the MCP server
# survives and transparently relaunches a browser on the next tool call.
#
# SAFETY DEFAULT: if activity cannot be determined, do NOT reap. A missed reap
# costs memory; a wrong reap interrupts someone's work.
#
# Usage:
#   playwright-reap-idle.sh [idle_seconds]    # default 1200 (20 min)
#   playwright-reap-idle.sh 600 --dry-run     # report only, never kill
#   playwright-reap-idle.sh 1200 --verbose    # also report while still counting
#
# Output: SILENT when there is nothing to say, so it can be called on a tight
# poll loop without flooding a log. Always exits 0 — a reaper must never take
# its caller down with it.

set -u

readonly PROFILE_MARKER="mcp-chrome-"
readonly ARTIFACT_DIR_NAME=".playwright-mcp"
UNLOCK_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/playwright-unlock.sh"
readonly UNLOCK_SCRIPT

IDLE_SECONDS=1200
DRY_RUN=false
VERBOSE=false

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    --verbose) VERBOSE=true ;;
    ''|*[!0-9]*) ;;   # ignore anything that is not a bare number
    *) IDLE_SECONDS="$arg" ;;
  esac
done

say() { [ "${VERBOSE}" = true ] && echo "playwright-reap-idle: $*"; return 0; }

# Real browser processes only. A bare `pgrep -f "mcp-chrome-"` also matches any
# SHELL whose command line contains the marker — including the wrapper running
# the pgrep itself, observed live — so every candidate is confirmed to be a
# chrome-ish executable before it is counted, let alone killed.
find_browser_pids() {
  local pid comm
  for pid in $(pgrep -f "${PROFILE_MARKER}" 2>/dev/null); do
    comm="$(ps -o comm= -p "${pid}" 2>/dev/null | tr -d ' ')"
    case "${comm##*/}" in
      chrome|chromium|chromium-browser|headless_shell) echo "${pid}" ;;
    esac
  done
}

file_mtime() {  # epoch seconds; GNU stat then BSD stat
  stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null
}

# Where the MCP writes its artifacts: <server cwd>/.playwright-mcp. Derived from
# each browser process's own cwd rather than hardcoded, so a session launched
# outside ~/code is still handled. $PWD and ~/code are added as fallbacks.
artifact_dirs() {
  local pid cwd
  for pid in $1; do
    cwd="$(readlink /proc/"${pid}"/cwd 2>/dev/null)"
    [ -n "${cwd}" ] && echo "${cwd}/${ARTIFACT_DIR_NAME}"
  done
  echo "${PWD}/${ARTIFACT_DIR_NAME}"
  echo "${HOME}/code/${ARTIFACT_DIR_NAME}"
}

# Newest artifact mtime across every candidate dir. Empty when none is found.
last_activity() {
  local dir newest latest="" m
  while read -r dir; do
    [ -d "${dir}" ] || continue
    newest="$(ls -t "${dir}" 2>/dev/null | head -1)"
    [ -n "${newest}" ] || continue
    m="$(file_mtime "${dir}/${newest}")"
    case "${m}" in ''|*[!0-9]*) continue ;; esac
    [ -z "${latest}" ] && latest="${m}"
    [ "${m}" -gt "${latest}" ] && latest="${m}"
  done < <(artifact_dirs "$1" | sort -u)
  echo "${latest}"
}

main() {
  local pids count seen now idle_for

  pids="$(find_browser_pids)"
  if [ -z "${pids}" ]; then
    say "no browser running."
    return 0
  fi

  count="$(echo "${pids}" | grep -c .)"
  seen="$(last_activity "${pids}")"
  now="$(date +%s)"

  # Safety default: unknown activity is never grounds for a kill.
  if [ -z "${seen}" ]; then
    say "${count} browser process(es), but no ${ARTIFACT_DIR_NAME} artifacts found — cannot tell if it is in use, leaving it."
    return 0
  fi

  idle_for=$(( now - seen ))
  [ "${idle_for}" -lt 0 ] && idle_for=0

  if [ "${idle_for}" -lt "${IDLE_SECONDS}" ]; then
    say "browser in use ${idle_for}s ago (threshold ${IDLE_SECONDS}s) — leaving it."
    return 0
  fi

  if [ "${DRY_RUN}" = true ]; then
    echo "playwright-reap-idle: WOULD reap ${count} process(es) — no MCP activity for ${idle_for}s (>= ${IDLE_SECONDS}s). [dry run]"
    return 0
  fi

  if [ ! -x "${UNLOCK_SCRIPT}" ]; then
    echo "playwright-reap-idle: cannot reap — ${UNLOCK_SCRIPT} missing or not executable." >&2
    return 0
  fi

  echo "playwright-reap-idle: reaping ${count} process(es) — no MCP activity for ${idle_for}s."
  "${UNLOCK_SCRIPT}" >/dev/null 2>&1 || true
  return 0
}

main
exit 0
