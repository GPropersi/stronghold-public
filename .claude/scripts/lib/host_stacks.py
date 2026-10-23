#!/usr/bin/env python3
"""host_stacks.py — map Docker compose stacks to worktrees/branches, and reap idle ones.

Subcommands (via host-status.sh / stack-reap.sh):
  status [--json]                     one row per compose project: containers, worktree, branch, idle time
  reap [--min-idle-hours H] [--dry-run]
                                      stop (never remove) the containers of idle worktree stacks

Idle time of a stack = now - the newest of these signals for its worktree:
  - mtime of every modified/untracked file (git status) and of the git index (stage/commit activity)
  - the newest hostq event (enqueue/start/end) whose cwd or label names the worktree
  - a live process whose cwd is inside the worktree (idle = 0)
A stack is reapable only when ALL hold: its working_dir is a git worktree under */.claude/worktrees/,
it has at least one running container, idle >= the minimum, and no hostq job for it is queued or running.
Reaping is `docker stop` only: containers, images, volumes and the worktree's files are untouched, so
`make up` brings the stack back and no in-progress code or data is lost. Hubs, primary clones and
non-worktree projects (scratch, prod-like) are never reaped.

Every reap decision (including skips with reasons) is logged to $STACK_REAP_LOG with UTC time, epoch,
project, idle seconds, the deciding state, rc and duration of each docker call.

Env: STACK_REAP_MIN_IDLE_HOURS (default 6), STACK_REAP_LOG (default ~/.local/state/hostq/reap.log),
     HOSTQ_EVENTS (default ~/.local/state/hostq/events.jsonl), STACK_DOCKER (docker binary, test seam),
     STACK_PROC_DIR (default /proc, test seam), STACK_DOCKER_TIMEOUT (default 120).
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

DOCKER = os.environ.get("STACK_DOCKER", "docker")
PROC = os.environ.get("STACK_PROC_DIR", "/proc")
EVENTS = os.environ.get(
    "HOSTQ_EVENTS", os.path.expanduser("~/.local/state/hostq/events.jsonl")
)
LOG = os.environ.get(
    "STACK_REAP_LOG", os.path.expanduser("~/.local/state/hostq/reap.log")
)
DOCKER_TIMEOUT = int(os.environ.get("STACK_DOCKER_TIMEOUT", "120"))
WORKTREE_MARKER = "/.claude/worktrees/"
FMT = "{{.ID}}\t{{.Names}}\t{{.State}}\t{{.Label \"com.docker.compose.project\"}}\t{{.Label \"com.docker.compose.project.working_dir\"}}"


def utc(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg):
    now = int(time.time())
    line = f"{utc(now)} epoch={now} {msg}"
    print(line, file=sys.stderr)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(line + "\n")
    except OSError as exc:
        print(f"host_stacks: cannot write {LOG}: {exc}", file=sys.stderr)


def run(cmd, timeout=30):
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, check=False
    )


def list_containers():
    res = run([DOCKER, "ps", "-a", "--format", FMT], timeout=DOCKER_TIMEOUT)
    if res.returncode != 0:
        raise SystemExit(f"host_stacks: docker ps failed rc={res.returncode}: {res.stderr.strip()}")
    rows = []
    for line in res.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 5 and parts[3]:
            rows.append(dict(zip(("id", "name", "state", "project", "wd"), parts)))
    return rows


def git_out(wd, *args):
    res = run(["git", "-C", wd, *args])
    return res.stdout if res.returncode == 0 else ""


def newest_mtime(paths, base):
    newest = 0.0
    for rel in paths:
        try:
            newest = max(newest, os.lstat(os.path.join(base, rel)).st_mtime)
        except OSError:
            continue
    return newest


def hostq_activity(wd, slug):
    """Return (newest event epoch naming this worktree, True if a job is queued/running)."""
    newest, open_ids, seen = 0, set(), {}
    try:
        with open(EVENTS) as fh:
            for line in fh:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                cwd, label = ev.get("cwd", ""), ev.get("label", "")
                if ev.get("event") == "enqueue":
                    seen[ev["id"]] = (cwd, label)
                cwd, label = seen.get(ev.get("id"), (cwd, label))
                if not (cwd.startswith(wd) or f" {slug} " in f" {label} "):
                    continue
                newest = max(newest, int(ev.get("epoch", 0)))
                if ev.get("event") == "enqueue":
                    open_ids.add(ev["id"])
                elif ev.get("event") in ("end", "cancel"):
                    open_ids.discard(ev.get("id"))
    except OSError:
        pass
    return newest, bool(open_ids)


def live_cwd_pids(wd):
    """PIDs of processes whose cwd is inside the worktree."""
    pids = []
    try:
        entries = os.listdir(PROC)
    except OSError:
        return pids
    for ent in entries:
        if not ent.isdigit():
            continue
        try:
            cwd = os.readlink(os.path.join(PROC, ent, "cwd"))
        except OSError:
            continue
        if cwd == wd or cwd.startswith(wd + "/"):
            pids.append(int(ent))
    return pids


def worktree_info(wd, now):
    """Branch, last-activity epoch and the signal that set it, for a worktree path."""
    branch = git_out(wd, "branch", "--show-current").strip() or "(detached)"
    slug = os.path.basename(wd)
    signals = {}
    changed = [
        line[3:].split(" -> ")[-1]
        for line in git_out(wd, "status", "--porcelain", "-uall").splitlines()
    ]
    signals["file-edit"] = newest_mtime(changed, wd)
    gitdir = git_out(wd, "rev-parse", "--absolute-git-dir").strip()
    if gitdir:
        signals["git-index"] = newest_mtime(["index", "HEAD"], gitdir)
    hq_epoch, hq_open = hostq_activity(wd, slug)
    signals["hostq-event"] = float(hq_epoch)
    pids = live_cwd_pids(wd)
    if pids:
        signals["live-process"] = float(now)
    last_signal = max(signals, key=signals.get)
    return {
        "branch": branch,
        "slug": slug,
        "last_activity": int(signals[last_signal]),
        "last_signal": last_signal if signals[last_signal] else "none",
        "hostq_open": hq_open,
        "live_pids": pids,
    }


def build_stacks():
    now = int(time.time())
    projects = {}
    for row in list_containers():
        proj = projects.setdefault(
            row["project"], {"project": row["project"], "wd": row["wd"], "running": 0, "total": 0, "ids": []}
        )
        proj["total"] += 1
        if row["state"] == "running":
            proj["running"] += 1
            proj["ids"].append(row["id"])
    stacks = []
    for proj in sorted(projects.values(), key=lambda p: p["project"]):
        wd = proj["wd"]
        is_worktree = WORKTREE_MARKER in wd + "/" and os.path.exists(wd)
        proj["kind"] = "worktree" if is_worktree else "other"
        proj["now"] = now
        if is_worktree:
            info = worktree_info(wd, now)
            proj.update(info)
            proj["idle"] = max(0, now - info["last_activity"]) if info["last_activity"] else None
        else:
            proj.update(
                branch=git_out(wd, "branch", "--show-current").strip() if os.path.isdir(wd) else "?",
                slug=os.path.basename(wd), last_activity=0, last_signal="n/a",
                hostq_open=False, live_pids=[], idle=None,
            )
        stacks.append(proj)
    return stacks


def human(seconds):
    if seconds is None:
        return "-"
    h, rem = divmod(int(seconds), 3600)
    return f"{h // 24}d{h % 24:02d}h" if h >= 24 else f"{h}h{rem // 60:02d}m"


def cmd_status(args):
    stacks = build_stacks()
    if args.json:
        print(json.dumps(stacks, indent=2))
        return 0
    print(f"{'PROJECT':<46} {'RUN/ALL':<8} {'IDLE':<8} {'LAST':<13} BRANCH  (worktree)")
    for st in stacks:
        where = st["wd"].replace(os.path.expanduser("~/code/"), "")
        print(
            f"{st['project']:<46} {st['running']}/{st['total']:<6} {human(st['idle']):<8} "
            f"{st['last_signal']:<13} {st['branch']}  ({where})"
        )
    return 0


def reap_one(st, min_idle):
    proj = st["project"]
    base = f"project={proj} branch={st['branch']} idle={st['idle']}s min={min_idle}s signal={st['last_signal']}"
    if st["kind"] != "worktree":
        return log(f"skip {base} reason=not-a-worktree-stack")
    if st["running"] == 0:
        return log(f"skip {base} reason=nothing-running")
    if st["live_pids"]:
        return log(f"skip {base} reason=live-process pids={st['live_pids'][:5]}")
    if st["hostq_open"]:
        return log(f"skip {base} reason=hostq-job-queued-or-running")
    if st["idle"] is None or st["idle"] < min_idle:
        return log(f"skip {base} reason=not-idle-long-enough")
    if ARGS.dry_run:
        return log(f"would-reap {base} containers={st['running']}")
    started = time.time()
    res = run([DOCKER, "stop", *st["ids"]], timeout=DOCKER_TIMEOUT)
    err = res.stderr.strip().replace("\n", " | ")[:300]
    log(
        f"reaped {base} containers={st['running']} rc={res.returncode} "
        f"duration={time.time() - started:.1f}s" + (f" err={err}" if res.returncode else "")
    )
    return res.returncode


def cmd_reap(args):
    min_idle = int(float(args.min_idle_hours) * 3600)
    log(f"reap-start min_idle_hours={args.min_idle_hours} dry_run={args.dry_run}")
    failures = 0
    for st in build_stacks():
        rc = reap_one(st, min_idle)
        failures += 1 if rc else 0
    log(f"reap-end failures={failures}")
    return 1 if failures else 0


def main():
    global ARGS
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_status = sub.add_parser("status")
    p_status.add_argument("--json", action="store_true")
    p_reap = sub.add_parser("reap")
    p_reap.add_argument(
        "--min-idle-hours", default=os.environ.get("STACK_REAP_MIN_IDLE_HOURS", "6")
    )
    p_reap.add_argument("--dry-run", action="store_true")
    ARGS = parser.parse_args()
    return {"status": cmd_status, "reap": cmd_reap}[ARGS.cmd](ARGS)


if __name__ == "__main__":
    sys.exit(main())
