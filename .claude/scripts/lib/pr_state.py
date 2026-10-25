#!/usr/bin/env python3
"""Core of pr-state.sh: one answer for "did this PR merge, and what is CI doing?" (stdlib only).

    pr_state.py [--stronghold <root>] <repo-dir> <pr-number> [--wait] [--timeout S] [--interval S]

Reads the PR once (state, merge commit, auto-merge, mergeStateStatus, status-check rollup), keeps the LATEST
attempt of each check (reruns and duplicate runs collapse by workflow+name), and prints a compact summary plus one
final `VERDICT:` line. With --wait it re-reads until the PR is MERGED/CLOSED, CI has failed, or the timeout hits,
printing only when the verdict line changes. Never trust a background `gh pr checks --watch` for the merged state:
this reads the PR itself.

Verdicts: MERGED | CLOSED | OPEN-CI-FAILED | OPEN-CI-RUNNING | OPEN-BLOCKED | OPEN-READY
A merged PR with a failing check still prints MERGED, then a `LATE-FAILURE:` line per failing check (a run that
finished after the merge, or a rerun): investigate those, they are not a merge blocker.

Env: PR_STATE_GH  gh wrapper to run (default gh-bot.sh beside this script's parent); it runs with the repo
                  directory as cwd because the bot installation is picked from the cwd's origin.
Exit: 0 merged | 1 closed unmerged | 3 open, CI failed | 5 still open at --wait timeout (or open without --wait,
      CI not failed) | 2 usage | 4 unavailable (no slug, gh failed)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB_DIR))

import wf_lint_base as base  # noqa: E402

GH_TIMEOUT = 90
FIELDS = "state,mergedAt,mergeCommit,mergeStateStatus,autoMergeRequest,headRefOid,statusCheckRollup,url"
MERGED_EXIT, CLOSED_EXIT, FAILED_EXIT, USAGE_EXIT, UNAVAILABLE_EXIT, PENDING_EXIT = 0, 1, 3, 2, 4, 5
BAD_CONCLUSIONS = {"FAILURE", "TIMED_OUT", "CANCELLED", "STARTUP_FAILURE", "ACTION_REQUIRED", "ERROR"}
GOOD_CONCLUSIONS = {"SUCCESS", "NEUTRAL", "SKIPPED"}


class Unavailable(Exception):
    pass


def check_key(item: dict) -> str:
    name = item.get("name") or item.get("context") or "?"
    return f'{item.get("workflowName") or ""}|{name}'


def check_name(item: dict) -> str:
    name = item.get("name") or item.get("context") or "?"
    workflow = item.get("workflowName")
    return f"{workflow} / {name}" if workflow and not name.startswith(workflow) else name


def check_bucket(item: dict) -> str:
    """pass | fail | running for one rollup entry (CheckRun or StatusContext)."""
    if "state" in item and "status" not in item:  # StatusContext
        state = item["state"].upper()
        return "pass" if state == "SUCCESS" else "fail" if state in BAD_CONCLUSIONS else "running"
    if item.get("status") != "COMPLETED":
        return "running"
    conclusion = (item.get("conclusion") or "").upper()
    if conclusion in GOOD_CONCLUSIONS:
        return "pass"
    return "fail" if conclusion in BAD_CONCLUSIONS else "running"


def latest_checks(rollup: list[dict]) -> list[dict]:
    """Keep the most recently started attempt of each check, so a rerun supersedes the run it replaces."""
    latest: dict[str, dict] = {}
    for item in rollup:
        stamp = item.get("startedAt") or item.get("completedAt") or ""
        key = check_key(item)
        if key not in latest or stamp >= (latest[key].get("startedAt") or latest[key].get("completedAt") or ""):
            latest[key] = item
    return list(latest.values())


def summarize(pr: dict) -> tuple[str, list[str], int]:
    """(verdict, output lines, exit code) for one PR read."""
    checks = latest_checks(pr.get("statusCheckRollup") or [])
    buckets = {"pass": [], "fail": [], "running": []}
    for item in checks:
        buckets[check_bucket(item)].append(item)
    counts = f'{len(buckets["pass"])} pass, {len(buckets["fail"])} fail, {len(buckets["running"])} running'
    auto = "on" if pr.get("autoMergeRequest") else "off"
    state = pr.get("state", "?")
    merge_commit = (pr.get("mergeCommit") or {}).get("oid", "")
    lines = [f'PR {pr.get("url", "")}', f'state: {state}  mergeState: {pr.get("mergeStateStatus")}  auto-merge: {auto}']
    if state == "MERGED":
        verdict, code = "MERGED", MERGED_EXIT
        lines.append(f'merged: {pr.get("mergedAt")}  commit: {merge_commit[:8]}')
    elif state == "CLOSED":
        verdict, code = "CLOSED", CLOSED_EXIT
    elif buckets["fail"]:
        verdict, code = "OPEN-CI-FAILED", FAILED_EXIT
    elif buckets["running"]:
        verdict, code = "OPEN-CI-RUNNING", PENDING_EXIT
    elif pr.get("mergeStateStatus") in ("CLEAN", "HAS_HOOKS", "UNSTABLE"):
        verdict, code = "OPEN-READY", PENDING_EXIT
    else:
        verdict, code = "OPEN-BLOCKED", PENDING_EXIT
    lines.append(f"checks (latest attempt each): {counts}")
    for item in buckets["running"]:
        lines.append(f"  RUNNING: {check_name(item)}")
    for item in buckets["fail"]:
        prefix = "LATE-FAILURE" if state == "MERGED" else "FAILED"
        lines.append(f'  {prefix}: {check_name(item)}  {item.get("detailsUrl") or ""}'.rstrip())
    lines.append(f"VERDICT: {verdict}")
    return verdict, lines, code


class Reader:
    def __init__(self, root: Path, repo: str) -> None:
        self.dir = root / repo
        ctx = base.Context(root, Path(os.environ.get("HOME", str(Path.home()))), base.load_config(root))
        self.slug = ctx.seam(repo).token("Repo slug")
        if not self.slug:
            raise Unavailable(f'missing seam key "Repo slug" in {self.dir / "CLAUDE.md"}')
        self.gh = os.environ.get("PR_STATE_GH") or str(LIB_DIR.parent / "gh-bot.sh")

    def read(self, number: int) -> dict:
        args = ["pr", "view", str(number), "--repo", self.slug, "--json", FIELDS]
        try:
            result = subprocess.run(
                [self.gh, *args], cwd=self.dir, text=True, capture_output=True, check=False, timeout=GH_TIMEOUT
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Unavailable(f"gh could not run: {exc}") from exc
        if result.returncode != 0:
            raise Unavailable(f"gh pr view failed: {(result.stderr or result.stdout).strip()}")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise Unavailable(f"gh pr view returned non-JSON: {exc}") from exc


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="pr-state.sh", description=__doc__.split("\n\n")[0])
    parser.add_argument("--stronghold", default=os.environ.get("STRONGHOLD_DIR") or str(LIB_DIR.parents[2]))
    parser.add_argument("repo_dir")
    parser.add_argument("pr_number", type=int)
    parser.add_argument("--wait", action="store_true", help="re-read until merged/closed/CI failed or --timeout")
    parser.add_argument("--timeout", type=int, default=1700, help="seconds to wait with --wait (default 1700)")
    parser.add_argument("--interval", type=int, default=30, help="seconds between reads with --wait (default 30)")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return USAGE_EXIT if exc.code else 0
    try:
        reader = Reader(Path(args.stronghold), args.repo_dir)
        deadline = time.monotonic() + args.timeout
        last_verdict = ""
        while True:
            verdict, lines, code = summarize(reader.read(args.pr_number))
            if verdict != last_verdict or not args.wait:
                print("\n".join(lines), flush=True)
            last_verdict = verdict
            terminal = verdict in ("MERGED", "CLOSED", "OPEN-CI-FAILED")
            if not args.wait or terminal:
                return code
            if time.monotonic() + args.interval > deadline:
                print(f"TIMEOUT: still {verdict} after {args.timeout}s", flush=True)
                return PENDING_EXIT
            time.sleep(args.interval)
    except Unavailable as exc:
        print(f"pr-state: {exc}", file=sys.stderr)
        return UNAVAILABLE_EXIT


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
