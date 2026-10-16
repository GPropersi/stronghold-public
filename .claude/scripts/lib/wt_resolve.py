#!/usr/bin/env python3
"""Worktree resolver core for wt-resolve.sh (stdlib only).

Resolves which checkout holds a plan's branch from `git worktree list --porcelain`, following
the CLAUDE.md Worktree Discipline part (1). Prints one JSON object; exit 0 ok, 3 on an
--assert mismatch. The bash entrypoint validates usage, the primary clone and the Plans bucket.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import wf_frontmatter  # noqa: E402


def git_out(cwd: str, *args: str) -> str:
    result = subprocess.run(["git", "-C", cwd, *args], text=True, capture_output=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def porcelain_blocks(primary: str) -> list[dict]:
    """One dict per worktree block: {path, branch (None if detached), prunable}. First = primary."""
    result = subprocess.run(
        ["git", "-C", primary, "worktree", "list", "--porcelain"], text=True, capture_output=True
    )
    if result.returncode != 0 or not result.stdout.strip():
        print(
            f"wt-resolve.sh: git worktree list failed in {primary}: {result.stderr.strip()}",
            file=sys.stderr,
        )
        sys.exit(4)
    out = result.stdout.strip()
    blocks: list[dict] = []
    current: dict | None = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            current = {"path": line[len("worktree ") :], "branch": None, "prunable": False}
            blocks.append(current)
        elif current is not None and line.startswith("branch "):
            current["branch"] = line[len("branch ") :].removeprefix("refs/heads/")
        elif current is not None and (line == "prunable" or line.startswith("prunable ")):
            current["prunable"] = True
    return blocks


def usable(block: dict) -> bool:
    return not block["prunable"] and os.path.isdir(block["path"])


def expand(value: str, stronghold: str) -> str:
    """`~` expands via $HOME; any other relative value is stronghold-relative."""
    value = os.path.expanduser(value)
    if not os.path.isabs(value):
        value = os.path.join(stronghold, value)
    return os.path.realpath(value)


def resolve(opts: argparse.Namespace) -> tuple[dict, str]:
    stronghold = os.path.realpath(opts.stronghold)
    primary = os.path.realpath(opts.primary)
    problems: list[str] = []
    branch = opts.branch
    worktree = opts.worktree
    fm: dict = {}
    if opts.plan:
        fm = wf_frontmatter.parse(opts.plan)
        branch = fm.get("branch") or None
        worktree = fm.get("worktree") or None
        plan_repo = fm.get("repo")
        if plan_repo is not None and plan_repo != opts.bucket:
            problems.append("plan-repo-mismatch")

    is_stronghold = opts.repo == "stronghold" or fm.get("repo") == "stronghold"
    if is_stronghold:
        primary = stronghold

    blocks = porcelain_blocks(primary)
    primary_path = os.path.realpath(blocks[0]["path"])
    primary = primary_path

    def result(kind: str, branch_value: str, source: str, abs_path: str | None) -> dict:
        rel = None
        if kind == "linked" and abs_path:
            rel = os.path.relpath(abs_path, stronghold)
        return {
            "repo": opts.repo,
            "branch": branch_value,
            "worktree_abs": abs_path,
            "worktree_rel": rel,
            "primary": primary,
            "kind": kind,
            "source": source,
            "problems": problems,
        }

    if not branch:
        current = git_out(primary, "branch", "--show-current")
        return result("primary", current, "current-branch", primary_path), current

    if is_stronghold or branch == opts.default_branch:
        if worktree:
            problems.append("stale-worktree")
        return result("primary", branch, "porcelain", primary_path), branch

    linked = [b for b in blocks[1:] if usable(b)]
    if worktree:
        wanted = expand(worktree, stronghold)
        if any(os.path.realpath(b["path"]) == wanted and b["branch"] == branch for b in linked):
            return result("linked", branch, "plan-frontmatter", wanted), branch
        problems.append("stale-worktree")

    if blocks and blocks[0]["branch"] == branch:
        return result("primary", branch, "porcelain", primary_path), branch
    for block in linked:
        if block["branch"] == branch:
            return result("linked", branch, "porcelain", os.path.realpath(block["path"])), branch
    return result("none", branch, "porcelain", None), branch


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--stronghold", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--primary", required=True)
    parser.add_argument("--default-branch", default="main")
    parser.add_argument("--plan")
    parser.add_argument("--bucket")
    parser.add_argument("--branch")
    parser.add_argument("--worktree")
    parser.add_argument("--assert", dest="assert_branch", action="store_true")
    opts = parser.parse_args()
    data, branch = resolve(opts)
    code = 0
    if opts.assert_branch:
        if data["kind"] == "none":
            data["problems"].append("assert-skipped-no-worktree")
        else:
            actual = git_out(data["worktree_abs"], "branch", "--show-current")
            if actual != branch:
                print(
                    f"wt-resolve.sh: {data['worktree_abs']} is on '{actual}', expected '{branch}'",
                    file=sys.stderr,
                )
                code = 3
    print(json.dumps(data))
    return code


if __name__ == "__main__":
    sys.exit(main())
