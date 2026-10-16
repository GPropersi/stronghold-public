#!/usr/bin/env python3
"""Workflow lint core: runs the check groups and prints findings (stdlib only).

Usage (via workflow-lint.sh): wf_lint.py --root <stronghold> [--home <dir>] [--repo <dir>]
                                         [--group seam|worktree|plans|hygiene|hooks|skills|all]
                                         [--json] [--staged]

Output: `severity<TAB>check-id<TAB>location<TAB>message` lines, or with --json an array of
{severity,id,location,message}. Exit: 0 no errors, 1 at least one error, 2 usage.
`severity_overrides` in .claude/seam-keys.json downgrade a check's `error` to the override severity
(staging for fixes that land in a later plan step); they never raise a severity.
--repo scopes the seam/worktree/plans groups to one repo directory; the stronghold-wide groups
(hygiene, hooks, skills) then run only when named with --group.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import wf_lint_base as base
import wf_lint_hooks_skills
import wf_lint_hygiene
import wf_lint_plans
import wf_lint_seam
import wf_lint_worktree

GROUPS = {
    "seam": wf_lint_seam.run,
    "worktree": wf_lint_worktree.run,
    "plans": wf_lint_plans.run,
    "hygiene": wf_lint_hygiene.run,
    "hooks": wf_lint_hooks_skills.run_hooks,
    "skills": wf_lint_hooks_skills.run_skills,
}
REPO_GROUPS = ("seam", "worktree", "plans")


def apply_overrides(findings: list[base.Finding], overrides: dict) -> list[base.Finding]:
    out: list[base.Finding] = []
    for finding in findings:
        override = overrides.get(finding.id)
        if finding.severity == "error" and isinstance(override, dict):
            severity = override.get("severity", "warn")
            if severity in base.SEVERITY_ORDER and severity != "error":
                finding = base.Finding(severity, finding.id, finding.location, finding.message, finding.repo)
        out.append(finding)
    return out


def run(ctx: base.Context, group: str) -> list[base.Finding]:
    if group == "all":
        names = list(REPO_GROUPS) if ctx.repo_filter else list(GROUPS)
    else:
        names = [group]
    findings: list[base.Finding] = []
    for name in names:
        try:
            result = GROUPS[name](ctx)
        except Exception as error:  # a lint must report odd repo content, never traceback
            findings.append(base.Finding("error", "lint.internal-error", f"group {name}",
                                         f"check group crashed: {type(error).__name__}: {error}"))
            continue
        if ctx.repo_filter and name in REPO_GROUPS:
            result = [f for f in result if f.repo == ctx.repo_filter]
        findings.extend(result)
    findings = apply_overrides(findings, ctx.cfg["severity_overrides"])
    return sorted(set(findings), key=lambda f: f.sort_key())


def usage(message: str) -> int:
    print(f"workflow-lint: {message}", file=sys.stderr)
    return 2


def main(argv: list[str]) -> int:
    root: Path | None = None
    home = Path(os.environ.get("HOME", "/"))
    repo: str | None = None
    group = "all"
    as_json = False
    staged = False
    args = iter(argv[1:])
    for arg in args:
        if arg in ("-h", "--help"):
            print(__doc__)
            return 0
        if arg in ("--root", "--home", "--repo", "--group"):
            value = next(args, None)
            if value is None:
                return usage(f"{arg} needs a value")
            if arg == "--root":
                root = Path(value)
            elif arg == "--home":
                home = Path(value)
            elif arg == "--repo":
                repo = value
            else:
                group = value
        elif arg == "--json":
            as_json = True
        elif arg == "--staged":
            staged = True
        else:
            return usage(f"unknown option '{arg}'")
    if root is None:
        return usage("--root is required")
    if group != "all" and group not in GROUPS:
        return usage(f"unknown group '{group}' (seam|worktree|plans|hygiene|hooks|skills|all)")
    if not root.is_dir():
        return usage(f"--root {root} is not a directory")
    ctx = base.Context(root, home, base.load_config(root), repo, staged)
    if repo is not None and repo not in ctx.repos:
        return usage(f"unknown repo '{repo}' (no git checkout under {root})")
    findings = run(ctx, group)
    if as_json:
        print(json.dumps([dict(zip(("severity", "id", "location", "message"), f.row())) for f in findings]))
    else:
        for finding in findings:
            print("\t".join(re.sub(r"[\t\r\n]", " ", field) for field in finding.row()))
    return 1 if any(f.severity == "error" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
