# shellcheck shell=bash
# shellcheck disable=SC2034
# Canonical fail-closed Cloudflare Access gate check. Host-agnostic: no helpers, no $0.
# Byte-identical copies of the marked block are kept in other repos by a workflow-lint rule.
# >>> access-gate (canonical: .claude/scripts/lib/access-gate.sh) >>>
# access_gate_check <hostname>: returns 0 gated, 1 open (200, no challenge),
# 2 unreachable, 3 unconfirmed (anything else, incl. empty hostname).
# Sets ACCESS_GATE_CODE to the observed HTTP status (empty when unreachable).
access_gate_check() {
  ACCESS_GATE_CODE=""
  local host="${1:-}" headers line lower val rest auth
  if [ -z "$host" ]; then
    return 3
  fi
  if ! headers="$(curl -sS -I --max-time 10 "https://${host}/" 2>/dev/null)"; then
    return 2
  fi
  ACCESS_GATE_CODE="$(printf '%s' "$headers" | awk 'toupper($1) ~ /^HTTP/ {c=$2; sub(/\r$/, "", c)} END{print c}')"
  while IFS= read -r line || [ -n "$line" ]; do
    lower="${line,,}"
    case "$lower" in
      cf-access*|x-cf-access*)
        return 0
        ;;
      location:*)
        val="${line#*:}"
        val="${val#"${val%%[![:space:]]*}"}"
        case "$val" in
          *://*) ;;
          *) continue ;;
        esac
        rest="${val#*://}"
        auth="${rest%%[/?#$'\r']*}"
        if [[ "$auth" =~ ^(.*):[0-9]+$ ]]; then
          auth="${BASH_REMATCH[1]}"
        fi
        auth="${auth,,}"
        if [[ "$auth" =~ ^[a-z0-9.-]+$ ]]; then
          case "$auth" in
            cloudflareaccess.com|*.cloudflareaccess.com) return 0 ;;
          esac
        fi
        ;;
    esac
  done <<< "$headers"
  if [ "$ACCESS_GATE_CODE" = "200" ]; then
    return 1
  fi
  return 3
}
# <<< access-gate <<<
