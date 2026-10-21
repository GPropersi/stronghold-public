#!/usr/bin/env bash
#
# quick-tunnel.sh — a generic, ephemeral Cloudflare *quick tunnel* for ANY local
# port, from anywhere in the stronghold. Token-free, no account, no DNS, no router
# port-forward: cloudflared dials OUT to Cloudflare's edge and gets a throwaway
# https://<random>.trycloudflare.com URL that proxies to a port on THIS host.
#
# Docker-image-only by design (no host cloudflared binary needed): it runs the
# pinned `cloudflare/cloudflared` image, matching how app-main already tunnels.
# Host-port reachability differs by OS, so we branch:
#   - Linux:  `--network host`  + target http://localhost:<port>   (reaches 127.0.0.1 services)
#   - macOS/other (Docker Desktop): host.docker.internal:<port>    (Desktop proxies to the host)
#
# State is derived entirely from Docker (container name prefix `qtunnel-`) — no
# files written, so nothing to clean up and no sandbox-write surface.
#
# This is the GENERIC layer. app-main keeps its own container-integrated
# `make tunnel` (it force-builds the stack + prints seeded logins, and targets an
# INTERNAL compose port). Use this for any other service reachable on a host port.
#
# Usage (prefer the stronghold `make` wrappers so it runs unsandboxed):
#   make tunnel PORT=8080 [NAME=foo]     ->  quick-tunnel.sh start 8080 [foo]
#   make tunnel-stop NAME=foo            ->  quick-tunnel.sh stop foo   (NAME=all for every one)
#   make tunnels                         ->  quick-tunnel.sh list
#
# SECURITY: a trycloudflare URL is PUBLIC and UNAUTHENTICATED for as long as it
# runs — anyone with the URL reaches your local port. Only expose things that are
# safe to expose (or are themselves authenticated), and stop the tunnel when done.
set -euo pipefail

CFLARED_IMAGE="cloudflare/cloudflared:2026.6.1"   # pinned; matches app-main's compose
NAME_PREFIX="qtunnel-"
TRYCF_RE='https://[a-z0-9-]+\.trycloudflare\.com'

# HARD POLICY: never expose a terminal through a quick tunnel. A trycloudflare URL
# is PUBLIC + UNAUTHENTICATED, so a web terminal behind it is an open shell to the
# internet. `start` refuses (no override) if the target port serves a known web
# terminal — use the Access-gated `remote-shell` skill / remote-shell-tunnel.sh, or Tailscale SSH.
TERM_PROC_RE='ttyd|wetty|gotty|shellinabox|tmate'
TERM_HTTP_RE='ttyd|gotty\.js|gotty|wetty|shellinabox|hterm'

RED='\033[0;31m'; GRN='\033[0;32m'; YEL='\033[0;33m'; CYA='\033[0;36m'; NC='\033[0m'
info()  { printf "${CYA}==>${NC} %s\n" "$*"; }
ok()    { printf "${GRN}OK:${NC} %s\n" "$*"; }
warn()  { printf "${YEL}WARN:${NC} %s\n" "$*" >&2; }
fatal() { printf "${RED}FATAL:${NC} %s\n" "$*" >&2; exit 1; }
need()  { command -v "$1" >/dev/null 2>&1 || fatal "'$1' is required but not installed."; }

sanitize() { printf '%s' "$1" | tr -c 'A-Za-z0-9_-' '-' | sed 's/-*$//'; }

url_of() {  # <container-name> -> prints the trycloudflare URL if present yet ('' if not)
  # `|| true`: grep exits non-zero until the URL appears; under `set -e` an
  # unguarded `url="$(url_of …)"` would otherwise abort the whole script.
  docker logs "$1" 2>&1 | grep -oE "$TRYCF_RE" | head -1 || true
}

running() {  # <container-name> -> 0 if a container with that exact name is up
  docker ps --format '{{.Names}}' | grep -qx "$1"
}

listening_proc() {  # <port> -> process name(s) bound to the port (best-effort, host-local)
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$1" -sTCP:LISTEN 2>/dev/null | awk 'NR>1{print $1}' | sort -u
  elif command -v ss >/dev/null 2>&1; then
    ss -ltnpH "sport = :$1" 2>/dev/null | grep -oE 'users:\(\("[^"]+' | sed 's/.*"//' | sort -u
  fi
}

# HARD refusal (no override) if the target port is a web terminal. Two signals:
# the listening process name, and an HTTP fingerprint of the served page.
refuse_if_terminal() {  # <port>
  local port="$1" proc body
  proc="$(listening_proc "$port" 2>/dev/null || true)"
  if [ -n "$proc" ] && printf '%s' "$proc" | grep -qiE "$TERM_PROC_RE"; then
    fatal "REFUSING: a web-terminal process ($(printf '%s' "$proc" | tr '\n' ' ')) is listening on localhost:${port}.
       A quick tunnel is PUBLIC + UNAUTHENTICATED — never expose a shell this way.
       Use the Access-gated 'remote-shell' skill / .claude/scripts/remote-shell-tunnel.sh, or Tailscale SSH, instead."
  fi
  if command -v curl >/dev/null 2>&1; then
    body="$(curl -s --max-time 3 "http://localhost:${port}/" 2>/dev/null | head -c 4000 || true)"
    if [ -n "$body" ] && printf '%s' "$body" | grep -qiE "$TERM_HTTP_RE"; then
      fatal "REFUSING: localhost:${port} looks like a web terminal (ttyd/gotty/wetty/shellinabox).
       A quick tunnel is PUBLIC + UNAUTHENTICATED — never tunnel a shell.
       Use the Access-gated 'remote-shell' skill / .claude/scripts/remote-shell-tunnel.sh, or Tailscale SSH, instead."
    fi
  fi
}

# Set DOCKER_NET_ARGS (array) + TARGET_URL for reaching a host port from the container.
os_target() {
  local port="$1"
  case "$(uname -s)" in
    Linux)
      DOCKER_NET_ARGS=(--network host)
      TARGET_URL="http://localhost:${port}"
      ;;
    *)  # macOS / Docker Desktop: host networking is unsupported; use the host alias.
      DOCKER_NET_ARGS=(--add-host=host.docker.internal:host-gateway)
      TARGET_URL="http://host.docker.internal:${port}"
      ;;
  esac
}

cmd_start() {
  local port="${1:-}" name="${2:-}"
  case "$port" in
    ''|*[!0-9]*) fatal "start needs a numeric PORT. Usage: quick-tunnel.sh start <port> [name]  (e.g. make tunnel PORT=8080)";;
  esac
  [ -n "$name" ] || name="port${port}"
  name="$(sanitize "$name")"
  local cname="${NAME_PREFIX}${name}"

  # POLICY GATE: never tunnel a terminal (public + unauthenticated). No override.
  refuse_if_terminal "$port"

  need docker
  if running "$cname"; then
    warn "a tunnel named '$name' is already running — reusing it:"
    print_result "$name" "$port" "$(url_of "$cname")"
    return 0
  fi

  os_target "$port"
  info "starting quick tunnel '$name'  ->  ${TARGET_URL}"
  docker run -d --rm --name "$cname" "${DOCKER_NET_ARGS[@]}" \
    "$CFLARED_IMAGE" tunnel --no-autoupdate --url "$TARGET_URL" >/dev/null \
    || fatal "docker failed to start the cloudflared container."

  local url=""
  for _ in $(seq 1 45); do
    url="$(url_of "$cname")"
    if [ -n "$url" ]; then break; fi
    if ! running "$cname"; then
      docker logs "$cname" 2>&1 | tail -15 >&2 || true
      fatal "the tunnel container exited before a URL appeared — is anything listening on localhost:${port}?"
    fi
    sleep 1
  done
  if [ -z "$url" ]; then
    docker logs "$cname" 2>&1 | tail -15 >&2 || true
    fatal "no trycloudflare URL after 45s — see the log tail above."
  fi
  print_result "$name" "$port" "$url"
}

print_result() {  # <name> <port> <url>
  local name="$1" port="$2" url="$3"
  echo
  ok "quick tunnel '$name' is live:"
  echo
  echo "  🌐  ${url}   ->   localhost:${port}"
  echo
  warn "PUBLIC + UNAUTHENTICATED while running — anyone with this URL reaches localhost:${port}."
  echo "     stop it when done:  make tunnel-stop NAME=${name}"
}

cmd_stop() {
  local target="${1:-all}"
  need docker
  if [ "$target" = "all" ]; then
    local names; names="$(docker ps --format '{{.Names}}' | grep "^${NAME_PREFIX}" || true)"
    if [ -z "$names" ]; then info "no active quick tunnels."; return 0; fi
    printf '%s\n' "$names" | while read -r n; do
      if docker rm -f "$n" >/dev/null 2>&1; then ok "stopped '${n#"$NAME_PREFIX"}'"; fi
    done
    return 0
  fi
  local cname
  cname="${NAME_PREFIX}$(sanitize "$target")"
  if docker rm -f "$cname" >/dev/null 2>&1; then ok "stopped '$target'"; else warn "no tunnel named '$target' is running."; fi
}

cmd_list() {
  need docker
  local names; names="$(docker ps --format '{{.Names}}' | grep "^${NAME_PREFIX}" || true)"
  if [ -z "$names" ]; then info "no active quick tunnels."; return 0; fi
  info "active quick tunnels:"
  printf '%s\n' "$names" | while read -r n; do
    local url; url="$(url_of "$n")"
    printf '  %-18s %s\n' "${n#"$NAME_PREFIX"}" "${url:-<starting…>}"
  done
}

case "${1:-}" in
  start) shift; cmd_start "$@" ;;
  stop)  shift; cmd_stop  "$@" ;;
  list)  cmd_list ;;
  *) fatal "usage: quick-tunnel.sh start <port> [name] | stop <name|all> | list" ;;
esac
