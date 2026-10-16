#!/usr/bin/env bash
#
# mock-server.sh
#
# Serves the central plans store (~/code/plans) over plain HTTP on localhost so
# mock HTML files can be rendered + screenshotted with the Playwright MCP.
#
# Why this exists: the documented UI Mockup Protocol (plan-creator SKILL.md,
# "Rendering mockups to PNG") says to `browser_navigate` to `file://<mock>.html`.
# The pinned Playwright MCP (@playwright/mcp@0.0.78, see .mcp.json) BLOCKS the
# file: protocol outright:
#     Error: Access to "file:" protocol is blocked.
# so every mock screenshot needs an http:// origin instead. This is that origin.
#
# Scope is deliberately narrow and local-only:
#   * binds 127.0.0.1 ONLY (never 0.0.0.0) — nothing leaves this machine
#   * document root is fixed to ~/code/plans (tracked, non-secret plan artifacts)
#   * read-only static serving via python3's stdlib http.server
#
# Usage:
#   mock-server.sh start [port]   # default port 8799; idempotent
#   mock-server.sh stop
#   mock-server.sh status
#
# Then navigate the Playwright MCP to:
#   http://127.0.0.1:8799/<repo>/open/<topic>/mocks/<name>.html

set -uo pipefail

readonly DEFAULT_PORT=8799
readonly ROOT="${HOME}/code/plans"
readonly PIDFILE="/tmp/claude/mock-server.pid"
readonly LOGFILE="/tmp/claude/mock-server.log"

running_pid() {
    [[ -f "$PIDFILE" ]] || return 1
    local pid
    pid="$(cat "$PIDFILE" 2>/dev/null)"
    [[ -n "$pid" ]] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    printf '%s' "$pid"
}

port_of() {
    [[ -f "${PIDFILE}.port" ]] && cat "${PIDFILE}.port" || printf '%s' "$DEFAULT_PORT"
}

start() {
    local port="${1:-$DEFAULT_PORT}"
    mkdir -p /tmp/claude

    if [[ ! -d "$ROOT" ]]; then
        echo "mock-server: document root $ROOT does not exist" >&2
        exit 1
    fi

    local pid
    if pid="$(running_pid)"; then
        echo "mock-server: already running (pid $pid) at http://127.0.0.1:$(port_of)/"
        return 0
    fi

    nohup python3 -m http.server "$port" --bind 127.0.0.1 --directory "$ROOT" \
        >"$LOGFILE" 2>&1 &
    local new_pid=$!
    echo "$new_pid" >"$PIDFILE"
    echo "$port" >"${PIDFILE}.port"

    # Give it a moment to bind, then confirm it is actually up.
    local tries=0
    while (( tries < 20 )); do
        if curl -sf -o /dev/null "http://127.0.0.1:${port}/"; then
            echo "mock-server: serving $ROOT at http://127.0.0.1:${port}/ (pid $new_pid)"
            return 0
        fi
        tries=$((tries + 1))
        sleep 0.1
    done

    echo "mock-server: failed to start — see $LOGFILE" >&2
    cat "$LOGFILE" >&2
    exit 1
}

stop() {
    local pid
    if ! pid="$(running_pid)"; then
        echo "mock-server: not running — nothing to do"
        rm -f "$PIDFILE" "${PIDFILE}.port"
        return 0
    fi
    kill "$pid" 2>/dev/null
    rm -f "$PIDFILE" "${PIDFILE}.port"
    echo "mock-server: stopped (pid $pid)"
}

status() {
    local pid
    if pid="$(running_pid)"; then
        echo "mock-server: running (pid $pid) at http://127.0.0.1:$(port_of)/ serving $ROOT"
    else
        echo "mock-server: not running"
    fi
}

case "${1:-status}" in
    start) shift; start "${1:-$DEFAULT_PORT}" ;;
    stop) stop ;;
    status) status ;;
    *) echo "usage: mock-server.sh {start [port]|stop|status}" >&2; exit 2 ;;
esac
