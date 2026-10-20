#!/usr/bin/env bash
#
# plans-ui.sh
#
# Launcher for the read-only plans dashboard (lib/plans_ui.py).
#
# Binds 127.0.0.1 only. Readiness is the server's own "listening on" line in the
# log plus a live launched pid, never an HTTP probe, so another service answering
# on the port cannot be mistaken for a successful start.
#
# Usage:
#   plans-ui.sh start [port]   # default port 8801; idempotent
#   plans-ui.sh stop
#   plans-ui.sh status
#
# Environment (all optional):
#   PLANS_UI_PIDFILE     pid file (default /tmp/claude/plans-ui.pid); the port and
#                        launch config live beside it as .port and .config
#   PLANS_UI_LOG         log file (default /tmp/claude/plans-ui.log)
#   PLANS_UI_PLANS_DIR   plans directory (default $HOME/code/plans)
#   PLANS_UI_ALLOW_HOST  extra allowed Host value (e.g. plans.owner.app)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
readonly DEFAULT_PORT=8801
readonly PIDFILE="${PLANS_UI_PIDFILE:-/tmp/claude/plans-ui.pid}"
readonly PORTFILE="${PIDFILE}.port"
readonly CONFIGFILE="${PIDFILE}.config"
readonly LOGFILE="${PLANS_UI_LOG:-/tmp/claude/plans-ui.log}"
readonly PLANS_DIR="${PLANS_UI_PLANS_DIR:-$HOME/code/plans}"

running_pid() {
    [[ -f "$PIDFILE" ]] || return 1
    local pid
    pid="$(cat "$PIDFILE" 2>/dev/null)"
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    printf '%s' "$pid"
}

port_of() {
    if [[ -f "$PORTFILE" ]]; then cat "$PORTFILE"; else printf '%s' "$DEFAULT_PORT"; fi
}

hosts_of() {
    if [[ -f "$CONFIGFILE" ]]; then cat "$CONFIGFILE"; else printf '%s' "unknown"; fi
}

clean_files() {
    rm -f "$PIDFILE" "$PORTFILE" "$CONFIGFILE"
}

start() {
    local port="$1"
    if [[ ! "$port" =~ ^[0-9]+$ ]]; then
        echo "plans-ui: invalid port '$port'" >&2
        exit 2
    fi
    mkdir -p "$(dirname "$PIDFILE")" "$(dirname "$LOGFILE")"

    if [[ ! -d "$PLANS_DIR" ]]; then
        echo "plans-ui: plans dir $PLANS_DIR does not exist" >&2
        exit 1
    fi

    local pid
    if pid="$(running_pid)"; then
        echo "plans-ui: already running (pid $pid) at http://127.0.0.1:$(port_of)/ (allowed hosts: $(hosts_of)); run stop first to change the port or PLANS_UI_ALLOW_HOST"
        return 0
    fi
    clean_files

    local hosts="127.0.0.1:${port}, localhost:${port}"
    local -a extra=()
    if [[ -n "${PLANS_UI_ALLOW_HOST:-}" ]]; then
        extra=(--allow-host "$PLANS_UI_ALLOW_HOST")
        hosts="${hosts}, ${PLANS_UI_ALLOW_HOST}"
    fi

    nohup python3 "$SCRIPT_DIR/lib/plans_ui.py" --port "$port" --bind 127.0.0.1 \
        --plans-dir "$PLANS_DIR" "${extra[@]}" >"$LOGFILE" 2>&1 &
    local new_pid=$!
    echo "$new_pid" >"$PIDFILE"
    echo "$port" >"$PORTFILE"
    echo "$hosts" >"$CONFIGFILE"

    local tries=0
    while (( tries < 50 )); do
        if ! kill -0 "$new_pid" 2>/dev/null; then
            break
        fi
        if grep -qF "listening on http://127.0.0.1:${port}/" "$LOGFILE" 2>/dev/null; then
            echo "plans-ui: serving $PLANS_DIR at http://127.0.0.1:${port}/ (pid $new_pid, allowed hosts: $hosts)"
            return 0
        fi
        tries=$((tries + 1))
        sleep 0.1
    done

    echo "plans-ui: failed to start; log $LOGFILE:" >&2
    cat "$LOGFILE" >&2
    kill "$new_pid" 2>/dev/null
    clean_files
    exit 1
}

stop() {
    local pid
    if ! pid="$(running_pid)"; then
        echo "plans-ui: not running, nothing to do"
        clean_files
        return 0
    fi
    kill -TERM "$pid" 2>/dev/null
    local tries=0
    while (( tries < 30 )); do
        if ! kill -0 "$pid" 2>/dev/null; then
            clean_files
            echo "plans-ui: stopped (pid $pid)"
            return 0
        fi
        tries=$((tries + 1))
        sleep 0.1
    done
    echo "plans-ui: pid $pid still running after SIGTERM" >&2
    exit 1
}

status() {
    local pid
    if pid="$(running_pid)"; then
        echo "plans-ui: running (pid $pid) at http://127.0.0.1:$(port_of)/ serving $PLANS_DIR (allowed hosts: $(hosts_of))"
    else
        echo "plans-ui: not running"
    fi
}

case "${1:-status}" in
    start) shift; start "${1:-$DEFAULT_PORT}" ;;
    stop) stop ;;
    status) status ;;
    *) echo "usage: plans-ui.sh {start [port]|stop|status}" >&2; exit 2 ;;
esac
