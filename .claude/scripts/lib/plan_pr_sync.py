#!/usr/bin/env python3
"""Record which of a plan's linked pull requests have merged or closed, so the plans dashboard can show
completed plans whose PRs are still in review.

Usage: plan_pr_sync.py [--dry-run] [<plans-dir>]

For every plan whose `pr_urls:` frontmatter holds a PR not yet in `pr_done:`, ask GitHub for the PR state
(through gh-bot.sh, run inside the sub-repo checkout whose origin matches the PR's owner/repo) and add MERGED
or CLOSED PRs to the `pr_done:` frontmatter key (space-separated). Idempotent. A PR whose state cannot be
read (no matching checkout, gh failure, timeout) is logged to stderr and left as in review.
Exit: 0 ok | 2 usage.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import plan_pr_link

DONE_KEY = "pr_done"
STATES_DONE = ("MERGED", "CLOSED")
GH_TIMEOUT = 30
SCRIPTS = Path(__file__).resolve().parent.parent
STRONGHOLD = Path(os.environ.get("STRONGHOLD_DIR") or SCRIPTS.parent.parent)
GH = os.environ.get("PR_SYNC_GH") or str(SCRIPTS / "gh-bot.sh")
ORIGIN_RE = re.compile(r"github\.com[:/]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?$")


def frontmatter_values(lines: list[str], key: str) -> tuple[int | None, list[str]]:
    """Index of the `key:` line inside the frontmatter (None when absent) and its space-separated values."""
    if not lines or lines[0].rstrip() != "---":
        return None, []
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            break
        if lines[i].startswith(f"{key}:"):
            return i, lines[i][len(key) + 1 :].strip().strip("\"'").split()
    return None, []


def checkouts() -> dict[str, Path]:
    """owner/repo -> sub-repo checkout directory under the stronghold, from each checkout's origin URL."""
    found: dict[str, Path] = {}
    for git_dir in sorted(STRONGHOLD.glob("*/.git")):
        out = subprocess.run(
            ["git", "-C", str(git_dir.parent), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=GH_TIMEOUT, check=False,
        )
        match = ORIGIN_RE.search(out.stdout.strip())
        if out.returncode == 0 and match:
            found[match.group(1)] = git_dir.parent
    return found


def pr_state(url: str, cwd: Path) -> str | None:
    start = time.monotonic()
    try:
        out = subprocess.run(
            [GH, "pr", "view", url, "--json", "state", "--jq", ".state"],
            cwd=cwd, capture_output=True, text=True, timeout=GH_TIMEOUT, check=False,
        )
    except subprocess.TimeoutExpired:
        print(f"plan-pr-sync: {url}: gh timed out after {GH_TIMEOUT}s", file=sys.stderr)
        return None
    took = time.monotonic() - start
    if out.returncode != 0:
        print(f"plan-pr-sync: {url}: gh rc={out.returncode} in {took:.1f}s: {out.stderr.strip()}", file=sys.stderr)
        return None
    return out.stdout.strip() or None


def write_done(path: Path, lines: list[str], done: list[str]) -> None:
    idx, _ = frontmatter_values(lines, DONE_KEY)
    if idx is not None:
        lines[idx] = f"{DONE_KEY}: " + " ".join(done)
    else:
        urls_idx, _ = frontmatter_values(lines, plan_pr_link.KEY)
        assert urls_idx is not None
        lines.insert(urls_idx + 1, f"{DONE_KEY}: " + " ".join(done))
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".plan-pr-sync-")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write("\n".join(lines))
        os.chmod(tmp, path.stat().st_mode & 0o777)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def sync(plans_dir: Path, dry_run: bool) -> int:
    repos = checkouts()
    seen: dict[str, str | None] = {}
    changed = 0
    for path in sorted(plans_dir.glob("*/*/*/*.md")):
        try:
            lines = path.read_text().split("\n")
        except OSError:
            continue
        _, urls = frontmatter_values(lines, plan_pr_link.KEY)
        if not urls:
            continue
        _, done = frontmatter_values(lines, DONE_KEY)
        new = list(done)
        for url in urls:
            if url in done or not plan_pr_link.PR_URL_RE.fullmatch(url):
                continue
            slug = "/".join(url.split("/")[3:5])
            cwd = repos.get(slug)
            if cwd is None:
                print(f"plan-pr-sync: {url}: no sub-repo checkout with origin {slug}", file=sys.stderr)
                continue
            if url not in seen:
                seen[url] = pr_state(url, cwd)
                print(f"plan-pr-sync: {url} {seen[url]}")
            state = seen[url]
            if state in STATES_DONE:
                new.append(url)
        if new != done:
            changed += 1
            if not dry_run:
                write_done(path, lines, new)
    print(f"plan-pr-sync: {changed} plan(s) {'would change' if dry_run else 'updated'}")
    return 0


def main(argv: list[str]) -> int:
    args = argv[1:]
    dry_run = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]
    if len(args) > 1:
        print(__doc__, file=sys.stderr)
        return 2
    plans_dir = Path(args[0]) if args else STRONGHOLD / "plans"
    return sync(plans_dir, dry_run)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
