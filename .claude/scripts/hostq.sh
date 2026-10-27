#!/usr/bin/env bash
#
# hostq.sh — host-wide FIFO job queue. Heavy jobs (test suites, builds) from ANY repo or Claude
# session wait their turn instead of piling onto a loaded host, and each job runs as a detached
# `systemd-run --user` unit so it survives the launching shell being killed (e.g. Claude Code's
# background-shell memory reaper). Self-contained on purpose: no stronghold or repo files are read,
# so the file can be vendored anywhere; nothing in a sub-repo needs to know it exists.
#
# Usage:
#   hostq.sh run [--label L] [--weight N] [--max-wait SEC] [--detach] -- <cmd> [args...]
#   hostq.sh status [--all] [--json]      queue, running jobs, host pressure (--all adds recent finished)
#   hostq.sh wait <id> [--max SEC]        follow a job's output until it ends; exits with its rc
#   hostq.sh result <id>                  print state + rc; exits with the job's rc (75 queue timeout)
#   hostq.sh log <id>                     print the job's output so far
#   hostq.sh cancel <id>                  stop a waiting or running job (rc 130)
#   hostq.sh events [-n N]                last N lines of the JSONL event log (default 20)
#   hostq.sh gc                           drop finished jobs older than HOSTQ_KEEP_DAYS and mark lost ones
#
# Semantics:
#   * Strict FIFO. A job starts when it is the oldest waiting job, the running weights plus its own
#     fit in HOSTQ_SLOTS (a job heavier than all slots runs alone), and host pressure is calm
#     (cpu `some avg10` <= HOSTQ_CPU_PSI_MAX, memory `some avg10` <= HOSTQ_MEM_PSI_MAX).
#   * Waiting is not failure: a queued job prints `hostq: QUEUED pos N ...` every HOSTQ_ANNOUNCE
#     seconds naming what it waits on. A wait longer than --max-wait (default HOSTQ_MAX_WAIT) ends
#     with exit 75 (EX_TEMPFAIL: still queued, NOT a failed test): rerun or `hostq.sh wait` later.
#   * `run` follows the job in the foreground and exits with its rc. SIGTERM/SIGHUP to the follower
#     DETACH it (the job keeps running; `hostq.sh wait|result <id>` later); SIGINT (Ctrl-C) cancels it.
#   * A command started inside a hostq job is passed through unqueued (HOSTQ_JOB_ID is set), so a
#     queued wrapper may call other wrapped scripts without deadlocking.
#   * Every state change is one JSONL line in $HOSTQ_DIR/events.jsonl: UTC `ts` + integer `epoch`
#     (same instant, always both) plus load1 and cpu/mem PSI at that moment.
#   * Limits: the unit cgroup covers host-side processes. Work a job hands to Docker (`docker exec`
#     workers) lives in Docker's cgroup, so stopping a job does not stop those. Therefore a job that is
#     cancelled, lost, or killed by a signal (rc >= 128) for a cwd inside a `.claude/worktrees/<slug>`
#     also gets that worktree's compose stack `docker stop`ped (lib/host_stacks.py stop; logged to
#     reap.log; HOSTQ_NO_STACK_STOP=1 disables). Containers are stopped, never removed.
#   * Attribution: a `cancel` event records the caller's process ancestry; a `signalled` event records
#     the signal, the unit cgroup's oom_kill count and the load/PSI at that moment (a handler cannot read
#     the sender pid, so "who sent it" is answered by cancel-vs-oom-vs-neither plus the ancestry).
#
# Env: HOSTQ_DIR (state, default ~/.local/state/hostq) · HOSTQ_SLOTS (2) · HOSTQ_CPU_PSI_MAX (50)
#      HOSTQ_MEM_PSI_MAX (5) · HOSTQ_MAX_WAIT (3600) · HOSTQ_ANNOUNCE (30) · HOSTQ_POLL (2)
#      HOSTQ_KEEP_DAYS (3) · HOSTQ_UNIT_PROPS (extra `-p` args for systemd-run, e.g. "-p CPUWeight=50")
#      test seams: HOSTQ_NO_UNIT=1 (detach with setsid instead of systemd-run) · HOSTQ_PSI_DIR
# Exit: job rc | 75 queue wait exceeded | 2 usage | 70 cannot use the state dir
set -uo pipefail

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
STATE_DIR="${HOSTQ_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/hostq}"
JOBS="$STATE_DIR/jobs"
LOCK="$STATE_DIR/lock"
EVENTS="$STATE_DIR/events.jsonl"
SLOTS="${HOSTQ_SLOTS:-2}"
CPU_PSI_MAX="${HOSTQ_CPU_PSI_MAX:-50}"
MEM_PSI_MAX="${HOSTQ_MEM_PSI_MAX:-5}"
MAX_WAIT="${HOSTQ_MAX_WAIT:-3600}"
ANNOUNCE="${HOSTQ_ANNOUNCE:-30}"
POLL="${HOSTQ_POLL:-2}"
KEEP_DAYS="${HOSTQ_KEEP_DAYS:-3}"
PSI_DIR="${HOSTQ_PSI_DIR:-/proc/pressure}"
LAUNCH_GRACE=30
EVENTS_MAX_BYTES=5242880

die() {
  echo "hostq.sh: $1" >&2
  exit "${2:-2}"
}

is_uint() { [[ "${1:-}" =~ ^[0-9]+$ ]]; }

usage() {
  sed -n '2,/^set /{/^set /!p}' "$SELF" | sed 's/^# \{0,1\}//'
}

now() { printf '%(%s)T' -1; }

iso_utc() { date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ; }

# Local wall time WITH its numeric offset, so a printed time is never ambiguous.
local_time() { date -d "@$1" '+%Y-%m-%d %H:%M:%S %z'; }

human_dur() {
  local s=$1
  if [ "$s" -ge 3600 ]; then
    printf '%dh%02dm' $((s / 3600)) $((s % 3600 / 60))
  elif [ "$s" -ge 60 ]; then
    printf '%dm%02ds' $((s / 60)) $((s % 60))
  else
    printf '%ds' "$s"
  fi
}

init_state() {
  umask 077
  mkdir -p "$JOBS" 2>/dev/null || die "cannot create state dir $STATE_DIR" 70
  [ -w "$STATE_DIR" ] || die "state dir $STATE_DIR is not writable" 70
  : >>"$LOCK" 2>/dev/null || die "cannot create lock file $LOCK" 70
}

# ---- job files: one small file per key under $JOBS/<id>/ -------------------------------------
jset() { # id key value  (atomic replace)
  printf '%s\n' "$3" >"$JOBS/$1/.$2.tmp" && mv -f "$JOBS/$1/.$2.tmp" "$JOBS/$1/$2"
}

jget() { # id key -> value or empty
  local f="$JOBS/$1/$2"
  [ -r "$f" ] && printf '%s' "$(<"$f")"
  return 0
}

jnum() { # id key -> the integer value, or the JSON literal null
  local v
  v="$(jget "$1" "$2")"
  if [[ "$v" =~ ^-?[0-9]+$ ]]; then printf '%s' "$v"; else printf 'null'; fi
}

list_jobs() {
  local d
  for d in "$JOBS"/*/; do
    [ -d "$d" ] || continue
    basename -- "$d"
  done | sort
}

pid_start() { # field 22 of /proc/<pid>/stat: guards against pid reuse
  local stat
  local -a fields
  stat="$(cat "/proc/$1/stat" 2>/dev/null)" || return 1
  stat="${stat##*) }"
  read -ra fields <<<"$stat"
  printf '%s' "${fields[19]}"
}

job_alive() { # a waiting/running job is alive while its worker pid (and start time) matches
  local id=$1 pid start cur enq
  pid="$(jget "$id" pid)"
  if [ -z "$pid" ]; then
    enq="$(jget "$id" enq)"
    is_uint "$enq" || return 1
    [ $(($(now) - enq)) -le "$LAUNCH_GRACE" ]
    return
  fi
  start="$(jget "$id" pidstart)"
  cur="$(pid_start "$pid")" || return 1
  [ -z "$start" ] || [ "$cur" = "$start" ]
}

# ---- events ----------------------------------------------------------------------------------
json_str() {
  local s=${1//\\/\\\\}
  s=${s//\"/\\\"}
  s="$(printf '%s' "$s" | tr -d '\000-\037')"
  printf '"%s"' "$s"
}

psi_avg10() { # resource -> the `some` avg10 value, 0 when PSI is unavailable
  local line
  line="$(sed -n 's/^some .*avg10=\([0-9.]*\).*/\1/p' "$PSI_DIR/$1" 2>/dev/null | head -n 1)"
  printf '%s' "${line:-0}"
}

load1() { awk '{print $1}' /proc/loadavg 2>/dev/null || echo 0; }

event() { # name id [key=value ...]  -> one JSONL line, flock-safe append
  local name=$1 id=$2 epoch kv key val fields
  shift 2
  epoch="$(now)"
  fields="\"ts\":\"$(iso_utc "$epoch")\",\"epoch\":$epoch,\"event\":$(json_str "$name"),\"id\":$(json_str "$id")"
  fields+=",\"load1\":$(load1),\"cpu_psi\":$(psi_avg10 cpu),\"mem_psi\":$(psi_avg10 memory)"
  for kv in "$@"; do
    key="${kv%%=*}"
    val="${kv#*=}"
    if [[ "$val" =~ ^-?[0-9]+(\.[0-9]+)?$ ]]; then
      fields+=",$(json_str "$key"):$val"
    else
      fields+=",$(json_str "$key"):$(json_str "$val")"
    fi
  done
  if [ -f "$EVENTS" ] && [ "$(stat -c %s "$EVENTS" 2>/dev/null || echo 0)" -gt "$EVENTS_MAX_BYTES" ]; then
    mv -f "$EVENTS" "$EVENTS.1"
  fi
  printf '{%s}\n' "$fields" >>"$EVENTS"
}

ancestry() { # pid -> "pid:comm <- pid:comm ..." up to 8 levels, for attribution in events
  local pid=$1 out="" n=0 comm stat
  while [ -n "$pid" ] && [ "$pid" -gt 1 ] && [ "$n" -lt 8 ]; do
    comm="$(tr -d '\n' <"/proc/$pid/comm" 2>/dev/null)" || break
    out+="${out:+ <- }$pid:$comm"
    stat="$(cat "/proc/$pid/stat" 2>/dev/null)" || break
    stat="${stat##*) }"
    pid="$(awk '{print $2}' <<<"$stat")"
    n=$((n + 1))
  done
  printf '%s' "$out"
}

# Docker work outlives a job's process tree: stop the compose stack of the worktree the job ran in.
# Detached and time-boxed because callers may hold the queue lock.
stop_stacks() { # id reason
  local id=$1 reason=$2 cwd helper
  [ "${HOSTQ_NO_STACK_STOP:-0}" = 1 ] && return 0
  cwd="$(jget "$id" cwd)"
  helper="$(dirname "$SELF")/lib/host_stacks.py"
  { [ -n "$cwd" ] && [ -r "$helper" ]; } || return 0
  setsid timeout 150 python3 "$helper" stop "$cwd" --reason "$reason:$id" >/dev/null 2>&1 </dev/null &
}

# ---- admission (call with the lock held) -----------------------------------------------------
mark_lost() {
  local id=$1
  jset "$id" state lost
  jset "$id" end "$(now)"
  [ -e "$JOBS/$id/rc" ] || jset "$id" rc 70
  event lost "$id" "label=$(jget "$id" label)" "was=worker-gone"
  stop_stacks "$id" lost
}

REASON=""
BEHIND=""
POSITION=0

try_admit() { # id -> 0 admitted (state set to running), 1 not admitted (REASON/BEHIND/POSITION set)
  local id=$1 jid st running_w=0 waiting_ahead=0 label
  REASON=""
  BEHIND=""
  POSITION=1
  for jid in $(list_jobs); do
    st="$(jget "$jid" state)"
    case "$st" in
      waiting | running) ;;
      *) continue ;;
    esac
    if [ "$jid" != "$id" ] && ! job_alive "$jid"; then
      mark_lost "$jid"
      continue
    fi
    label="$(jget "$jid" label)"
    if [ "$st" = running ]; then
      running_w=$((running_w + $(jget "$jid" weight)))
      BEHIND+="${BEHIND:+, }$label (running $(human_dur $(($(now) - $(jget "$jid" start)))))"
    elif [ "$jid" != "$id" ] && [[ "$jid" < "$id" ]]; then
      waiting_ahead=$((waiting_ahead + 1))
      BEHIND+="${BEHIND:+, }$label (waiting)"
    fi
  done
  POSITION=$((waiting_ahead + 1))
  if [ "$waiting_ahead" -gt 0 ]; then
    REASON="$waiting_ahead job(s) queued ahead"
    return 1
  fi
  local weight
  weight="$(jget "$id" weight)"
  if [ "$running_w" -gt 0 ] && [ $((running_w + weight)) -gt "$SLOTS" ]; then
    REASON="slots full (running weight $running_w of $SLOTS, this job needs $weight)"
    return 1
  fi
  local cpu mem
  cpu="$(psi_avg10 cpu)"
  mem="$(psi_avg10 memory)"
  if awk -v a="$cpu" -v m="$CPU_PSI_MAX" 'BEGIN{exit !(a>m)}'; then
    REASON="held: host cpu pressure avg10 ${cpu}% > ${CPU_PSI_MAX}%"
    return 1
  fi
  if awk -v a="$mem" -v m="$MEM_PSI_MAX" 'BEGIN{exit !(a>m)}'; then
    REASON="held: host memory pressure avg10 ${mem}% > ${MEM_PSI_MAX}%"
    return 1
  fi
  return 0
}

# ---- worker: runs inside the detached unit ---------------------------------------------------
cmd_worker() {
  local id=${1:-}
  [ -n "$id" ] && [ -d "$JOBS/$id" ] || die "worker: unknown job '$id'" 70
  init_state
  jset "$id" pid "$$"
  jset "$id" pidstart "$(pid_start "$$")"
  local label weight enq maxwait cwd
  label="$(jget "$id" label)"
  weight="$(jget "$id" weight)"
  enq="$(jget "$id" enq)"
  maxwait="$(jget "$id" maxwait)"
  cwd="$(jget "$id" cwd)"

  local kv
  if [ -r "$JOBS/$id/env" ]; then
    while IFS= read -r -d '' kv; do
      [[ "${kv%%=*}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
      case "${kv%%=*}" in
        _ | SHLVL | PWD | OLDPWD | BASHOPTS | SHELLOPTS | BASH_* | HOSTQ_JOB_ID) continue ;;
      esac
      # shellcheck disable=SC2163  # kv is a whole NAME=value assignment read from the caller's env dump
      export "$kv" 2>/dev/null || true
    done <"$JOBS/$id/env"
    rm -f "$JOBS/$id/env"
  fi

  exec 9>"$LOCK"
  local last_reason="" last_announce=0 t admitted=0
  while :; do
    flock -x 9
    if [ "$(jget "$id" state)" != waiting ]; then # cancelled while queued
      flock -u 9
      exit "$(jget "$id" rc)"
    fi
    if try_admit "$id"; then
      jset "$id" state running
      jset "$id" start "$(now)"
      event start "$id" "label=$label" "weight=$weight" "waited=$(($(now) - enq))"
      flock -u 9
      admitted=1
      break
    fi
    flock -u 9
    t="$(now)"
    if [ "$maxwait" -gt 0 ] && [ $((t - enq)) -ge "$maxwait" ]; then
      echo "hostq: still queued after $(human_dur $((t - enq))) ($REASON; behind: ${BEHIND:-none}). Not a failure, the job never started: resubmit when the host is quieter." >&2
      flock -x 9
      jset "$id" state timeout
      jset "$id" end "$t"
      jset "$id" rc 75
      event timeout "$id" "label=$label" "waited=$((t - enq))" "reason=$REASON"
      flock -u 9
      exit 75
    fi
    if [ "$REASON" != "$last_reason" ] || [ $((t - last_announce)) -ge "$ANNOUNCE" ]; then
      echo "hostq: QUEUED pos $POSITION for '$label' since $(local_time "$enq") (epoch $enq): $REASON; behind: ${BEHIND:-none}"
      if [ "$last_reason" = "" ]; then
        event queued "$id" "label=$label" "position=$POSITION" "reason=$REASON"
      fi
      last_reason="$REASON"
      last_announce="$t"
    fi
    sleep "$POLL"
  done
  [ "$admitted" = 1 ] || exit 70

  local -a argv
  mapfile -d '' -t argv <"$JOBS/$id/argv"
  echo "hostq: START '$label' (job $id) at $(local_time "$(now)"), waited $(human_dur $(($(now) - enq)))"
  local rc
  (
    cd "$cwd" 2>/dev/null || {
      echo "hostq: cannot cd to $cwd" >&2
      exit 127
    }
    # shellcheck disable=SC2030  # deliberately scoped to this subshell, the job's own process
    export HOSTQ_JOB_ID="$id"
    "${argv[@]}"
  )
  rc=$?
  local end dur start
  end="$(now)"
  start="$(jget "$id" start)"
  dur=$((end - start))
  flock -x 9
  jset "$id" end "$end"
  jset "$id" rc "$rc"
  [ "$(jget "$id" state)" = cancelled ] || jset "$id" state "done"
  event end "$id" "label=$label" "rc=$rc" "duration=$dur" "waited=$((start - enq))"
  if [ "$rc" -ge 128 ] && [ "$rc" -le 192 ]; then
    local cg oom=unknown
    cg="/sys/fs/cgroup$(cut -d: -f3- /proc/self/cgroup 2>/dev/null)/memory.events"
    [ -r "$cg" ] && oom="$(awk '$1=="oom_kill"{print $2}' "$cg")"
    event signalled "$id" "label=$label" "rc=$rc" "signal=$(kill -l $((rc - 128)) 2>/dev/null || echo '?')" \
      "unit_oom_kill=$oom" "state=$(jget "$id" state)"
    stop_stacks "$id" "signalled-rc$rc"
  fi
  flock -u 9
  echo "hostq: END '$label' rc=$rc duration $(human_dur "$dur")"
  exit "$rc"
}

# ---- launcher --------------------------------------------------------------------------------
unit_name() { printf 'hostq-%s' "$1"; }

systemd_usable() {
  [ "${HOSTQ_NO_UNIT:-0}" != 1 ] && command -v systemd-run >/dev/null 2>&1 || return 1
  # cron, sandboxes and `env -i` shells lack XDG_RUNTIME_DIR; the user manager's socket is still there.
  local runtime_dir
  runtime_dir="/run/user/$(id -u)"
  if [ -z "${XDG_RUNTIME_DIR:-}" ] && [ -d "$runtime_dir" ]; then
    export XDG_RUNTIME_DIR="$runtime_dir"
  fi
  systemctl --user show-environment >/dev/null 2>&1 || return 1
  # A user manager started before a group was added to this account (e.g. docker) never gives its units
  # that group, so the job would lose access the launching shell has. Fall back to setsid, which inherits
  # the shell's groups (restarting the user manager would kill every tmux window).
  local mine unit_groups g
  mine="$(id -G)"
  unit_groups=" $(systemd-run --user --quiet --collect --pipe --wait id -G 2>/dev/null) "
  for g in $mine; do
    case "$unit_groups" in
      *" $g "*) ;;
      *)
        SD_WHY="the systemd user manager predates group $g (restart it or re-login to fix; units would lack it)"
        return 1
        ;;
    esac
  done
}

spawn_worker() {
  local id=$1 out cwd
  out="$JOBS/$id/out"
  cwd="$(jget "$id" cwd)"
  if systemd_usable; then
    # The unit starts with a bare environment: hand it the resolved state dir and every HOSTQ_* setting
    # so the worker reads the same queue, limits and seams as the launcher.
    local -a setenv=(--setenv=PATH="$PATH" --setenv=HOME="$HOME" --setenv=HOSTQ_DIR="$STATE_DIR")
    local var
    for var in $(compgen -e | grep '^HOSTQ_' | grep -v -e '^HOSTQ_DIR$' -e '^HOSTQ_JOB_ID$' -e '^HOSTQ_UNIT_PROPS$'); do
      setenv+=("--setenv=$var=${!var}")
    done
    # shellcheck disable=SC2086  # HOSTQ_UNIT_PROPS is a deliberate word-split list of -p args
    systemd-run --user --quiet --collect --unit="$(unit_name "$id")" \
      --description="hostq $(jget "$id" label)" --working-directory="$cwd" \
      "${setenv[@]}" \
      -p "StandardOutput=append:$out" -p "StandardError=append:$out" \
      -p KillMode=control-group -p TimeoutStopSec=30 ${HOSTQ_UNIT_PROPS:-} \
      "$SELF" _worker "$id" >/dev/null || return 1
    jset "$id" mode unit
  else
    if [ "${HOSTQ_NO_UNIT:-0}" != 1 ]; then
      echo "hostq: note: ${SD_WHY:-systemd user manager unavailable}; using setsid (the job outlives its shell but is not cgroup-contained)" >&2
    fi
    setsid nohup "$SELF" _worker "$id" >>"$out" 2>&1 </dev/null &
    jset "$id" mode setsid
  fi
}

new_job_id() { printf '%019d-%04d' "$(date +%s%N)" "$((RANDOM % 10000))"; }

follow_job() { # id: stream output until the job has an rc, then exit with it
  local id=$1 out tpid rc st
  out="$JOBS/$id/out"
  tail -n +1 -F "$out" 2>/dev/null &
  tpid=$!
  trap 'kill "$tpid" 2>/dev/null; echo "hostq: detached from job '"$id"' (it keeps running): hostq.sh wait|result|cancel '"$id"'" >&2; exit 143' TERM HUP
  trap 'kill "$tpid" 2>/dev/null; cmd_cancel "$id" >/dev/null 2>&1; echo "hostq: cancelled job '"$id"'" >&2; exit 130' INT
  while [ ! -e "$JOBS/$id/rc" ]; do
    st="$(jget "$id" state)"
    if { [ "$st" = waiting ] || [ "$st" = running ]; } && ! job_alive "$id"; then
      sleep "$POLL"
      [ -e "$JOBS/$id/rc" ] && break
      ( flock -x 9; job_alive "$id" || mark_lost "$id" ) 9>>"$LOCK"
    fi
    sleep "$POLL"
  done
  sleep 0.3
  kill "$tpid" 2>/dev/null
  wait "$tpid" 2>/dev/null
  rc="$(jget "$id" rc)"
  exit "${rc:-70}"
}

cmd_run() {
  local label="" weight=1 maxwait="$MAX_WAIT" detach=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --label) [ $# -ge 2 ] || die "--label needs a value"; label="$2"; shift 2 ;;
      --weight) [ $# -ge 2 ] || die "--weight needs a value"; weight="$2"; shift 2 ;;
      --max-wait) [ $# -ge 2 ] || die "--max-wait needs a value"; maxwait="$2"; shift 2 ;;
      --detach) detach=1; shift ;;
      --) shift; break ;;
      -*) die "unknown flag '$1' (the command goes after --)" ;;
      *) break ;;
    esac
  done
  [ $# -gt 0 ] || die "no command given (usage: hostq.sh run [flags] -- <cmd> ...)"
  is_uint "$weight" && [ "$weight" -ge 1 ] || die "--weight must be a positive integer"
  is_uint "$maxwait" || die "--max-wait must be a non-negative integer (seconds; 0 = wait forever)"
  [ -n "$label" ] || label="$(basename -- "$1")"
  label="$(printf '%s' "$label" | tr -d '\000-\037')"

  # shellcheck disable=SC2031  # the subshell export in cmd_worker is a different process; this reads the inherited env
  if [ -n "${HOSTQ_JOB_ID:-}" ]; then # already inside a queued job: run inline, never queue twice
    exec "$@"
  fi

  init_state
  local id dir
  id="$(new_job_id)"
  dir="$JOBS/$id"
  mkdir -p "$dir" || die "cannot create job dir" 70
  : >"$dir/out"
  printf '%s\0' "$@" >"$dir/argv"
  env -0 >"$dir/env"
  jset "$id" label "$label"
  jset "$id" weight "$weight"
  jset "$id" maxwait "$maxwait"
  jset "$id" cwd "$PWD"
  jset "$id" cmd "$*"
  jset "$id" enq "$(now)"
  jset "$id" state waiting
  event enqueue "$id" "label=$label" "weight=$weight" "cmd=$*" "cwd=$PWD"
  spawn_worker "$id" || {
    jset "$id" state lost
    jset "$id" rc 70
    event spawn-failed "$id" "label=$label"
    die "could not start the job unit (systemd-run failed)" 70
  }
  echo "hostq: job $id '$label' submitted ($(jget "$id" mode)); log $dir/out" >&2
  if [ "$detach" = 1 ]; then
    echo "$id"
    return 0
  fi
  follow_job "$id"
}

# ---- inspection and control ------------------------------------------------------------------
require_job() {
  [ -n "${1:-}" ] || die "job id required"
  [ -d "$JOBS/$1" ] || die "no such job: $1" 2
}

cmd_wait() {
  local id="${1:-}" max=0
  require_job "$id"
  shift
  if [ "${1:-}" = --max ]; then
    is_uint "${2:-}" || die "--max needs seconds"
    max="$2"
  fi
  local started
  started="$(now)"
  local out="$JOBS/$id/out" tpid
  tail -n +1 -F "$out" 2>/dev/null &
  tpid=$!
  while [ ! -e "$JOBS/$id/rc" ]; do
    if [ "$max" -gt 0 ] && [ $(($(now) - started)) -ge "$max" ]; then
      kill "$tpid" 2>/dev/null
      echo "hostq: still $(jget "$id" state) after ${max}s of waiting" >&2
      return 75
    fi
    sleep "$POLL"
  done
  sleep 0.3
  kill "$tpid" 2>/dev/null
  wait "$tpid" 2>/dev/null
  return "$(jget "$id" rc)"
}

cmd_result() {
  local id="${1:-}"
  require_job "$id"
  local st rc
  st="$(jget "$id" state)"
  rc="$(jget "$id" rc)"
  if { [ "$st" = waiting ] || [ "$st" = running ]; } && ! job_alive "$id"; then
    st="lost (worker gone before finishing)"
    rc=70
  fi
  echo "job $id label=$(jget "$id" label) state=$st rc=${rc:-none}"
  case "$st" in
    waiting | running) return 1 ;;
  esac
  return "${rc:-70}"
}

cmd_log() {
  require_job "${1:-}"
  cat "$JOBS/$1/out"
}

cmd_cancel() {
  local id="${1:-}" st pid
  require_job "$id"
  st="$(jget "$id" state)"
  case "$st" in
    waiting | running) ;;
    *) echo "job $id already $st"; return 0 ;;
  esac
  pid="$(jget "$id" pid)"
  ( flock -x 9
    jset "$id" state cancelled
    jset "$id" end "$(now)"
    jset "$id" rc 130
    event cancel "$id" "label=$(jget "$id" label)" "was=$st" "caller=$(ancestry "$$")" ) 9>>"$LOCK"
  stop_stacks "$id" cancel
  if [ "$(jget "$id" mode)" = unit ]; then
    systemctl --user stop "$(unit_name "$id").service" >/dev/null 2>&1 || true
  elif [ -n "$pid" ]; then
    kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  fi
  echo "cancelled $id"
}

cmd_events() {
  local n=20
  [ "${1:-}" = -n ] && n="${2:-20}"
  is_uint "$n" || die "-n needs a number"
  [ -f "$EVENTS" ] && tail -n "$n" "$EVENTS"
  return 0
}

cmd_gc() {
  init_state
  ( flock -x 9
    local id st end cutoff
    cutoff=$(($(now) - KEEP_DAYS * 86400))
    for id in $(list_jobs); do
      st="$(jget "$id" state)"
      case "$st" in
        waiting | running)
          job_alive "$id" || mark_lost "$id" ;;
        *)
          end="$(jget "$id" end)"
          if is_uint "$end" && [ "$end" -lt "$cutoff" ]; then
            rm -rf -- "${JOBS:?}/$id"
          fi ;;
      esac
    done ) 9>>"$LOCK"
}

cmd_status() {
  local all=0 json=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --all) all=1 ;;
      --json) json=1 ;;
      *) die "unknown status flag '$1'" ;;
    esac
    shift
  done
  init_state
  cmd_gc
  local t id st rows=0 running_w=0 pos=0
  t="$(now)"
  local cpu mem
  cpu="$(psi_avg10 cpu)"
  mem="$(psi_avg10 memory)"
  if [ "$json" = 1 ]; then
    local first=1
    printf '{"now_epoch":%s,"now_utc":"%s","slots":%s,"cpu_psi":%s,"mem_psi":%s,"load1":%s,"jobs":[' \
      "$t" "$(iso_utc "$t")" "$SLOTS" "$cpu" "$mem" "$(load1)"
    for id in $(list_jobs); do
      st="$(jget "$id" state)"
      if [ "$all" = 0 ] && [ "$st" != waiting ] && [ "$st" != running ]; then continue; fi
      [ "$first" = 1 ] || printf ','
      first=0
      printf '{"id":%s,"label":%s,"state":%s,"weight":%s,"enq":%s,"start":%s,"end":%s,"rc":%s}' \
        "$(json_str "$id")" "$(json_str "$(jget "$id" label)")" "$(json_str "$st")" \
        "$(jnum "$id" weight)" "$(jnum "$id" enq)" "$(jnum "$id" start)" "$(jnum "$id" end)" "$(jnum "$id" rc)"
    done
    printf ']}\n'
    return 0
  fi
  echo "host: $(hostname)  now: $(iso_utc "$t") = $(local_time "$t") = epoch $t"
  echo "pressure (avg10): cpu ${cpu}% (hold > ${CPU_PSI_MAX}%)  memory ${mem}% (hold > ${MEM_PSI_MAX}%)  load1 $(load1)"
  printf '%-4s %-9s %-34s %-6s %-9s %-9s %s\n' pos state label weight waited running id
  for id in $(list_jobs); do
    st="$(jget "$id" state)"
    case "$st" in
      running)
        running_w=$((running_w + $(jget "$id" weight)))
        printf '%-4s %-9s %-34s %-6s %-9s %-9s %s\n' '-' running "$(jget "$id" label)" "$(jget "$id" weight)" \
          "$(human_dur $(($(jget "$id" start) - $(jget "$id" enq))))" "$(human_dur $((t - $(jget "$id" start))))" "$id"
        rows=$((rows + 1)) ;;
      waiting)
        pos=$((pos + 1))
        printf '%-4s %-9s %-34s %-6s %-9s %-9s %s\n' "$pos" waiting "$(jget "$id" label)" "$(jget "$id" weight)" \
          "$(human_dur $((t - $(jget "$id" enq))))" '-' "$id"
        rows=$((rows + 1)) ;;
      *)
        [ "$all" = 1 ] || continue
        printf '%-4s %-9s %-34s %-6s %-9s %-9s %s (rc %s, ended %s)\n' '-' "$st" "$(jget "$id" label)" "$(jget "$id" weight)" \
          '-' '-' "$id" "$(jget "$id" rc)" "$(local_time "$(jget "$id" end)")"
        rows=$((rows + 1)) ;;
    esac
  done
  [ "$rows" -gt 0 ] || echo "(no queued or running jobs)"
  echo "slots: running weight $running_w of $SLOTS"
}

main() {
  local sub="${1:-}"
  [ "$#" -gt 0 ] && shift
  case "$sub" in
    run) cmd_run "$@" ;;
    _worker) cmd_worker "$@" ;;
    status) cmd_status "$@" ;;
    wait) cmd_wait "$@" ;;
    result) cmd_result "$@" ;;
    log) cmd_log "$@" ;;
    cancel) cmd_cancel "$@" ;;
    events) cmd_events "$@" ;;
    gc) cmd_gc "$@" ;;
    -h | --help | help | "") usage ;;
    *) die "unknown command '$sub' (try --help)" ;;
  esac
}

main "$@"
