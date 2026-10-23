#!/usr/bin/env bash
#
# host-pressure.sh — was this host overloaded at a given moment? Reads the sysstat history (sar) so a
# test failure from ANY spoke or repo can be matched to host CPU/memory/swap pressure by its timestamp.
#
# Usage:
#   host-pressure.sh <timestamp> [--window <minutes>]
#
#   <timestamp>  a failure time, e.g. "2026-10-21 21:40:14" or "2026-10-21T21:40:14.578+00:00"
#   --window     minutes of samples to show before the moment (default 20)
#   TIME BASIS: the instant is resolved to epoch seconds. A Z/+hh:mm suffix is honoured (app-main failure
#   artifacts write true UTC: "...T21:40:14+00:00" = 14:40:14 PDT); a bare timestamp is read in the HOST zone
#   (timedatectl, override HOST_PRESSURE_TZ), never the caller's TZ. The output prints the moment in UTC, host
#   zone and epoch, and each sample time host-local with a numeric offset. sysstat is read via `sadf -U` (epoch):
#   its text clock is TZ-independent and says "PST" in summer, so it is never used. The window crosses
#   midnight (reads the previous/next day's sa file).
#
# Prints one row per sysstat sample (each row is the average over the interval ENDING at its time) and
# a verdict for the sample that covers the moment. Signals and heuristic thresholds:
#   CPU   PSI "some" %scpu >= 25, or 1-min load >= 2x CPUs   -> CPU oversubscribed
#   MEM   PSI "some" %smem >= 2 (interval) or >= 10 (last 10 s), major faults >= 1000/s, or swap-in >= 500/s
#                                                            -> memory thrashing (with swap full the thrash is
#                                                               page-cache refaults, so swap-in alone stays low)
#   IO    PSI "some" %sio >= 10                              -> I/O stalls
# A flag means tasks were waiting on the host. No flag is NOT proof of health when the interval is long:
# a 15 s stall inside a 10-minute sample is averaged away (the row says when the interval is coarse).
# Sampling is every 10 minutes by default; a 1-minute sysstat-collect.timer drop-in sharpens it
# (misc/infra-notes.md, "Host pressure history").
#
# Env: HOST_PRESSURE_SA_DIR (default /var/log/sysstat), HOST_PRESSURE_NCPU, HOST_PRESSURE_CSV (a file of
# concatenated `sadf -d` output, bypassing sadf: tests).
# Exit: 0 verdict printed | 2 usage error | 3 no sysstat data for that time
set -euo pipefail
export LC_ALL=C

die() {
  echo "host-pressure.sh: $1" >&2
  exit "${2:-2}"
}

SA_DIR="${HOST_PRESSURE_SA_DIR:-/var/log/sysstat}"
ncpu="${HOST_PRESSURE_NCPU:-$(nproc)}"
stamp=""
window=20
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h | --help)
      sed -n '2,/^set /{/^set /!p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    --window)
      [ "$#" -ge 2 ] || die "--window needs minutes"
      window="$2"
      shift 2
      ;;
    -*) die "unknown option: $1" ;;
    *)
      [ -z "$stamp" ] || die "one timestamp only"
      stamp="$1"
      shift
      ;;
  esac
done
[ -n "$stamp" ] || die "a timestamp is required"
case "$window" in '' | *[!0-9]*) die "--window must be whole minutes" ;; esac

# TIME BASIS: every instant below is epoch seconds. The host zone is pinned (TZ) so neither `date` nor the
# printed sample times depend on the caller's TZ, and `sadf -U` yields epoch, so sysstat's own clock text
# (TZ-independent and mislabelled "PST" in summer) is never parsed. A bare timestamp is read in the host
# zone; a Z/+hh:mm suffix is honoured. (A bare time in the repeated DST hour resolves to the first one:
# pass an explicit offset.)
HOST_TZ="${HOST_PRESSURE_TZ:-$(timedatectl show -p Timezone --value 2>/dev/null || true)}"
[ -n "$HOST_TZ" ] || HOST_TZ="$(cat /etc/timezone 2>/dev/null || true)"
[ -n "$HOST_TZ" ] || die "cannot determine the host timezone (set HOST_PRESSURE_TZ)"
export TZ="$HOST_TZ"

epoch="$(date -d "$stamp" '+%s' 2>/dev/null)" || die "cannot parse timestamp: $stamp"
start_e=$((epoch - window * 60))
# +11 minutes so the 10-minute sample that ENDS after the moment (the one covering it) is included.
end_e=$((epoch + 11 * 60))

# A sa file is named for the local day it started and holds samples up to ~00:00:12 of the next day, so
# look at the file of each boundary's day and of the day before it; rows are then filtered by epoch.
sa_files=()
for t in "$start_e" "$epoch" "$end_e"; do
  # Calendar arithmetic from noon of the local date: t-86400s lands two days back after a 23-hour
  # spring-forward day, and local midnight can fall in a DST gap in other zones.
  tday="$(date -d "@$t" '+%Y-%m-%d')"
  for day in "$tday" "$(date -d "$tday 12:00 -1 day" '+%Y-%m-%d')"; do
    f="$SA_DIR/sa$(date -d "$day 12:00" '+%d')"
    case " ${sa_files[*]:-} " in *" $f "*) ;; *) sa_files+=("$f") ;; esac
  done
done

collect() {
  if [ -n "${HOST_PRESSURE_CSV:-}" ]; then
    cat "$HOST_PRESSURE_CSV"
    return
  fi
  local f opts found=0
  for f in "${sa_files[@]}"; do
    [ -r "$f" ] || continue
    found=1
    for opts in "-q PSI" "-q LOAD" "-W -B -S" "-r"; do
      # shellcheck disable=SC2086  # opts is a fixed word list
      sadf -d -U "$f" -- $opts
    done
  done
  [ "$found" -eq 1 ] || die "no sysstat files found for that time (looked for: ${sa_files[*]})" 3
}

rows="$(collect | awk -F';' -v moment="$epoch" -v lo="$start_e" -v hi="$end_e" '
  /^# hostname/ {
    delete names
    for (i = 1; i <= NF; i++) { n = $i; sub(/^# /, "", n); names[i] = n }
    next
  }
  NF > 3 {
    ts = $3 + 0
    if (ts < lo || ts > hi) next
    if (!(ts in seen)) { seen[ts] = 1; order[++count] = ts }
    iv[ts] = $2
    for (i = 4; i <= NF; i++) val[ts, names[i]] = $i
  }
  function dash(x) { return (x == "") ? "-" : x }
  END {
    for (a = 2; a <= count; a++) {
      v = order[a]
      for (b = a - 1; b >= 1 && order[b] > v; b--) order[b + 1] = order[b]
      order[b + 1] = v
    }
    covering = ""
    before = ""
    for (k = 1; k <= count; k++) {
      if (order[k] >= moment) { covering = order[k]; break }
      before = order[k]
    }
    for (k = 1; k <= count; k++) {
      ts = order[k]
      mark = (ts == covering) ? "<--" : ((ts == before) ? "pre" : ".")
      printf "ROW %s %s %s %s %s %s %s %s %s %s %s %s %s %s %s\n", ts, dash(iv[ts]), dash(val[ts, "%scpu"]), dash(val[ts, "ldavg-1"]), dash(val[ts, "runq-sz"]), \
        dash(val[ts, "%smem"]), dash(val[ts, "%sio"]), dash(val[ts, "pswpin/s"]), dash(val[ts, "pswpout/s"]), dash(val[ts, "majflt/s"]), \
        dash(val[ts, "%swpused"]), dash(val[ts, "kbavail"]), dash(val[ts, "%smem-10"]), dash(val[ts, "%commit"]), mark
    }
  }')"

[ -n "$rows" ] || die "no samples between epoch $start_e and $end_e in: ${sa_files[*]}" 3

# Sample times become host-local with a numeric offset (disambiguates the repeated DST hour).
rows="$(echo "$rows" | while read -r tag ts rest; do echo "$tag $(date -d "@$ts" '+%H:%M:%S%z') $rest"; done)"

echo "host: $(hostname)  cpus: $ncpu  zone: $HOST_TZ"
echo "input:  $stamp"
echo "moment: $(date -u -d "@$epoch" '+%Y-%m-%d %H:%M:%S UTC') = $(date -d "@$epoch" '+%Y-%m-%d %H:%M:%S %Z (%:z)') = epoch $epoch"
echo "sample times below are host-local, ending each interval, with numeric UTC offset"
printf '%-13s %6s %6s %6s %5s %6s %6s %8s %8s %8s %6s %8s %7s %7s\n' time "int(s)" "cpu%" "load1" runq "mem%" "io%" "swpin/s" "swpout/s" "majflt/s" "swap%" "avail_GB" "mem10%" "commit%"
echo "$rows" | awk '{
  gb = $13 / 1048576
  printf "%-13s %6s %6s %6s %5s %6s %6s %8s %8s %8s %6s %8.1f %7s %7s %s\n", $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, gb, $14, $15, $16
}'

# Columns: mem%/cpu%/io% are the share of the INTERVAL tasks were stalled (PSI "some"); mem10% is the same
# over the 10 s BEFORE the sample was taken, so the "pre" row (last sample before the moment) shows how bad
# memory was seconds ahead of it. "pre" = last sample before the moment, "<--" = the sample covering it.
echo "$rows" | awk -v ncpu="$ncpu" '
  function judge(label,    flags) {
    flags = ""
    if ($4 + 0 >= 25 || $5 + 0 >= 2 * ncpu) flags = flags "CPU oversubscribed (cpu-PSI " $4 "%, load1 " $5 ", runq " $6 " on " ncpu " CPUs); "
    if ($7 + 0 >= 2 || $14 + 0 >= 10 || $11 + 0 >= 1000 || $9 + 0 >= 500) flags = flags "memory thrashing (mem-PSI " $7 "%, last-10s " $14 "%, major faults " $11 "/s, swap-in " $9 "/s, swap " $12 "% used, avail " sprintf("%.1f", $13 / 1048576) " GB); "
    if ($8 + 0 >= 10) flags = flags "I/O stalls (io-PSI " $8 "%); "
    if (flags == "") flags = "no pressure flag"
    printf "%s %s (interval %ss): %s\n", label, $2, $3, flags
    if (index(label, "covering") && $3 + 0 > 120) coarse = 1
  }
  $16 == "pre" { havepre = 1; judge("VERDICT sample before the moment, at") }
  $16 == "<--" { found = 1; judge("VERDICT covering sample ending at") }
  END {
    if (!found) print "VERDICT: no sample ends at or after that moment in the window."
    if (coarse) print "Note: the interval is coarse (a stall of tens of seconds is averaged away), so a missing flag would not prove a quiet host."
  }'
