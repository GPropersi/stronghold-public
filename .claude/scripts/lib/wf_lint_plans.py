"""Plans group of the workflow lint: frontmatter, lifecycle and layout of <root>/plans."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import wf_frontmatter
import wf_lint_base as base
import plan_lifecycle

STATES = ("open", "completed")
BUCKET_ENTRIES = {"open", "completed", "research"}
TOPIC_FOLDERS = {"reviews", "research", "mocks", "mockup", "mockups", "tmp", "screenshots"}
WT_RESOLVE = Path(__file__).resolve().parent.parent / "wt-resolve.sh"


def _topics(bucket_dir: Path, state: str) -> list[Path]:
    state_dir = bucket_dir / state
    try:
        return sorted(p for p in state_dir.iterdir() if p.is_dir())
    except OSError:
        return []


def _plan_files(topic: Path) -> list[Path]:
    return sorted(p for p in topic.glob("*.md") if p.is_file())


def _confirmed(ctx: base.Context, repo: str, branch: str, worktree: str) -> bool | None:
    """True/False from wt-resolve.sh --branch --worktree (stale-worktree absent/present); None if unknown."""
    env = {**os.environ, "STRONGHOLD_DIR": str(ctx.root), "HOME": str(ctx.home)}
    if branch.startswith("-") or any(c.isspace() or ord(c) < 32 for c in branch):
        return None
    try:
        result = subprocess.run(
            ["bash", str(WT_RESOLVE), repo, "--branch", branch, "--worktree", worktree],
            text=True, capture_output=True, env=env, check=False, timeout=30,
        )
        return "stale-worktree" not in json.loads(result.stdout)["problems"]
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
        return None


def _check_plan(ctx: base.Context, bucket: str, state: str, plan: Path) -> list[base.Finding]:
    out: list[base.Finding] = []
    meta = wf_frontmatter.parse(plan)
    location = ctx.rel(plan)
    repo = ctx.repo_for_bucket(bucket)
    if "repo" not in meta:
        out.append(base.Finding("warn", "plans.frontmatter-repo-matches-bucket", location,
                                f"plan has no `repo:` frontmatter (expected `{bucket}`)", repo))
    elif meta["repo"] != bucket:
        out.append(base.Finding("error", "plans.frontmatter-repo-matches-bucket", location,
                                f"`repo: {meta['repo']}` does not match the bucket directory plans/{bucket}/", repo))
    branch = meta.get("branch", "")
    worktree = meta.get("worktree", "")
    if bucket in ctx.cfg["fixed_buckets"].values():
        if branch:
            out.append(base.Finding("error", "plans.branch-absent-for-stronghold", location,
                                    "stronghold plans push to main and carry no `branch:`"))
    elif branch and repo:
        default = ctx.seam(repo).token("Default branch")
        if default and branch == default:
            out.append(base.Finding("error", "plans.branch-not-default", location,
                                    f"`branch: {branch}` is the repo's default branch (omit it for default-branch plans)", repo))
    if worktree:
        pattern = rf"^{re.escape(repo)}/\.claude/worktrees/[a-z0-9-]+$" if repo else None
        valid = pattern is not None and re.match(pattern, worktree) is not None
        if not valid:
            out.append(base.Finding("error", "plans.worktree-format", location,
                                    f"`worktree: {worktree}` must look like <repo>/.claude/worktrees/<slug>", repo))
        if not branch:
            out.append(base.Finding("error", "plans.worktree-implies-branch", location,
                                    "`worktree:` is set without `branch:`", repo))
        elif repo and valid and _confirmed(ctx, repo, branch, worktree) is False:
            out.append(base.Finding("warn", "plans.worktree-confirmed-by-porcelain", location,
                                    f"`worktree: {worktree}` is not on branch {branch} in `git worktree list` (cache is stale)", repo))
    if state == "completed":
        finished = meta.get("finished")
        if finished is False:
            out.append(base.Finding("error", "plans.completed-has-finished-true", location,
                                    "plan lives in completed/ but its Status says `finished: false`", repo))
        elif finished is None:
            out.append(base.Finding("warn", "plans.completed-has-finished-true", location,
                                    "plan lives in completed/ but has no `finished:` line in its Status block", repo))
    return out


def _check_bucket(ctx: base.Context, bucket_dir: Path) -> list[base.Finding]:
    out: list[base.Finding] = []
    bucket = bucket_dir.name
    repo = ctx.repo_for_bucket(bucket)
    for entry in sorted(bucket_dir.iterdir()):
        if entry.name not in BUCKET_ENTRIES:
            out.append(base.Finding("warn", "plans.layout-valid", ctx.rel(entry),
                                    "unexpected entry in a plans bucket (expected open/ completed/ research/)", repo))
    names: dict[str, set[str]] = {}
    for state in STATES:
        state_dir = bucket_dir / state
        if state_dir.is_dir():
            for entry in sorted(state_dir.iterdir()):
                if entry.is_file() and entry.name != ".gitkeep":
                    out.append(base.Finding("warn", "plans.layout-valid", ctx.rel(entry),
                                            f"loose file in {state}/ (plans live in {state}/<topic>/)", repo))
        for topic in _topics(bucket_dir, state):
            names.setdefault(topic.name, set()).add(state)
            for sub in sorted(p for p in topic.iterdir() if p.is_dir()):
                if sub.name not in TOPIC_FOLDERS:
                    out.append(base.Finding("warn", "plans.layout-valid", ctx.rel(sub),
                                            "unexpected folder in a topic (expected reviews/ research/ mocks/ tmp/ screenshots/)", repo))
            plans = _plan_files(topic)
            for plan in plans:
                out.extend(_check_plan(ctx, bucket, state, plan))
            if state == "open" and plan_lifecycle.is_review_only(topic):
                out.append(base.Finding("warn", "plans.open-topic-has-plan", ctx.rel(topic),
                                        "open topic is review-only, no plan (it belongs in completed/): run `make plan-sweep`", repo))
            if state == "open" and plans and all(wf_frontmatter.parse(p).get("finished") is True for p in plans):
                out.append(base.Finding("warn", "plans.open-topic-fully-finished", ctx.rel(topic),
                                        "every plan in this open topic is finished: move it to completed/", repo))
    for topic, states in sorted(names.items()):
        if len(states) > 1:
            out.append(base.Finding("warn", "plans.topic-split-across-states", f"plans/{bucket}",
                                    f"topic `{topic}` exists in both open/ and completed/", repo))
    return out


def run(ctx: base.Context) -> list[base.Finding]:
    plans_dir = ctx.root / "plans"
    if not plans_dir.is_dir():
        return []
    out: list[base.Finding] = []
    allowed = set(ctx.declared_buckets()) | set(ctx.cfg["fixed_buckets"].values())
    # an exempt repo (no bot script) still keeps its plans in the central store under its declared bucket
    allowed |= {ctx.bucket_of(repo) for repo in ctx.repos if ctx.is_exempt(repo) and ctx.bucket_of(repo)}
    for bucket_dir in sorted(p for p in plans_dir.iterdir() if p.is_dir()):
        repo = ctx.repo_for_bucket(bucket_dir.name)
        if ctx.repo_filter and repo != ctx.repo_filter:
            continue
        if bucket_dir.name not in allowed:
            out.append(base.Finding("warn", "plans.bucket-has-repo", ctx.rel(bucket_dir),
                                    f"plans/{bucket_dir.name}/ is not the `Plans bucket` of any repo", repo))
        out.extend(_check_bucket(ctx, bucket_dir))
    code, listing = base.git(ctx.root, "ls-files", "plans")
    if code == 0:
        for path in listing.splitlines():
            if re.match(r"^plans/[^/]+/[^/]+/[^/]+/tmp/", path):
                out.append(base.Finding("error", "plans.tmp-not-tracked", path,
                                        "tmp/ under a plan topic is transient: never track it"))
    return out
