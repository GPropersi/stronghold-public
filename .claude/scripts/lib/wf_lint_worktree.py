"""Worktree group of the workflow lint: Worktree* seam values and `git worktree` layout."""

from __future__ import annotations

import re
from pathlib import Path

import wf_lint_base as base

POLICIES = ("full", "build-only", "singleton-runtime")
PORT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=[0-9]+$")


def slug_dns(branch: str) -> str:
    """Python twin of lib/wt-lib.sh wt_slug_dns: lower-case, non-[a-z0-9-] -> '-', strip leading
    '-', cut to 40, strip trailing '-'."""
    slug = re.sub(r"[^a-z0-9-]", "-", branch.lower()).lstrip("-")[:40]
    return slug.rstrip("-")


def parse_porcelain(text: str) -> list[dict]:
    blocks: list[dict] = []
    for chunk in text.split("\n\n"):
        block: dict = {"path": "", "branch": None, "prunable": False}
        for line in chunk.splitlines():
            if line.startswith("worktree "):
                block["path"] = line[len("worktree ") :]
            elif line.startswith("branch refs/heads/"):
                block["branch"] = line[len("branch refs/heads/") :]
            elif line.startswith("prunable"):
                block["prunable"] = True
        if block["path"]:
            blocks.append(block)
    return blocks


def duplicate_branches(blocks: list[dict]) -> dict[str, list[str]]:
    seen: dict[str, list[str]] = {}
    for block in blocks:
        if block["branch"]:
            seen.setdefault(block["branch"], []).append(block["path"])
    return {branch: paths for branch, paths in seen.items() if len(paths) > 1}


def _loc(repo: str, line: int = 0) -> str:
    return f"{repo}/CLAUDE.md:{line}" if line else f"{repo}/CLAUDE.md"


def _value_checks(repo: str, seam: base.Seam) -> list[base.Finding]:
    out: list[base.Finding] = []
    policy = seam.token("Worktree policy")
    if policy and policy not in POLICIES:
        out.append(base.Finding("error", "worktree.policy-valid", _loc(repo, seam.line("Worktree policy")),
                                f"Worktree policy `{policy}` must be one of {', '.join(POLICIES)} or n/a", repo))
    if policy == "build-only" and not seam.token("Worktree allowed targets"):
        out.append(base.Finding("error", "worktree.policy-keys-complete", _loc(repo, seam.line("Worktree policy")),
                                "policy build-only needs a non-n/a `Worktree allowed targets`", repo))
    if policy == "singleton-runtime" and not seam.token("Worktree guarded targets"):
        out.append(base.Finding("error", "worktree.policy-keys-complete", _loc(repo, seam.line("Worktree policy")),
                                "policy singleton-runtime needs a non-n/a `Worktree guarded targets`", repo))
    link_value = (seam.value("Worktree link") or "").replace("`", " ")
    for item in ([] if link_value.strip() == "n/a" else link_value.split()):
        parts = Path(item).parts
        if item.startswith("/") or ".." in parts or (parts and parts[0] == ".git"):
            out.append(base.Finding("error", "worktree.link-paths-safe", _loc(repo, seam.line("Worktree link")),
                                    f"link path `{item}` must be relative, without .., and outside .git", repo))
    ports = seam.token("Worktree ports")
    if ports and not all(PORT_RE.match(item.strip()) for item in ports.split(",")):
        out.append(base.Finding("error", "worktree.ports-format", _loc(repo, seam.line("Worktree ports")),
                                f"Worktree ports `{ports}` must be ENV_KEY=BASE pairs separated by commas", repo))
    return out


def _supports_worktrees(ctx: base.Context, seam: base.Seam) -> bool:
    return any(seam.token(key) for key in ctx.cfg["worktree"])


def _layout_checks(ctx: base.Context, repo: str, declared: bool) -> list[base.Finding]:
    out: list[base.Finding] = []
    repo_dir = ctx.root / repo
    if declared:
        code, _ = base.git(repo_dir, "check-ignore", "-q", ".claude/worktrees/x/x")
        if code == 1:
            out.append(base.Finding("error", "worktree.gitignore-covers", f"{repo}/.gitignore",
                                    ".claude/worktrees/ is not git-ignored in this repo", repo))
    code, text = base.git(repo_dir, "worktree", "list", "--porcelain")
    if code != 0:
        return out
    blocks = parse_porcelain(text)
    managed = (repo_dir / ".claude" / "worktrees").resolve()
    for block in blocks[1:]:
        path = Path(block["path"])
        location = f"{repo}:{block['path']}"
        try:
            under = path.resolve().relative_to(managed)
        except ValueError:
            under = None
            out.append(base.Finding("error", "worktree.path-under-claude-worktrees", location,
                                    f"linked worktree {block['path']} is outside {repo}/.claude/worktrees/", repo))
        if block["prunable"]:
            out.append(base.Finding("warn", "worktree.not-prunable", location,
                                    f"worktree {block['path']} is prunable (run `git worktree prune`)", repo))
        if under is not None and block["branch"] and under.parts and under.parts[0] != slug_dns(block["branch"]):
            out.append(base.Finding("error", "worktree.slug-matches-branch", location,
                                    f"directory `{under.parts[0]}` should be `{slug_dns(block['branch'])}` for branch {block['branch']}", repo))
    for branch, paths in duplicate_branches(blocks).items():
        out.append(base.Finding("error", "worktree.one-branch-per-worktree", f"{repo}:{branch}",
                                f"branch {branch} is checked out in several worktrees: {', '.join(paths)}", repo))
    return out


def run(ctx: base.Context) -> list[base.Finding]:
    findings: list[base.Finding] = []
    for repo in ctx.scoped_repos():
        seam = ctx.seam(repo)
        declared = False
        if not ctx.is_exempt(repo) and seam.has_section:
            findings.extend(_value_checks(repo, seam))
            declared = _supports_worktrees(ctx, seam)
            if not declared:
                findings.append(base.Finding("info", "worktree.support-declared", _loc(repo),
                                             "no Worktree* seam key declares worktree support", repo))
        findings.extend(_layout_checks(ctx, repo, declared))
    return findings
