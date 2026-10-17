#!/usr/bin/env python3
"""Core of pr-closing-check.sh: compare the issues a PR will close with the issues it should close (stdlib only).

    pr_closing_check.py [--stronghold <root>] <repo-dir> <pr-number> [--expect N,N|none] [--fix]

GitHub parses the PR body with a keyword scanner: a sentence such as "this does not close #39" still closes #39 on
merge. The authoritative answer is the PR's `closingIssuesReferences`, so that is what is compared with --expect
(default none): an unexpected issue is reported with the body lines that mention it, a missing one is reported too.
--fix rewrites the closing keyword in front of each UNEXPECTED reference to the word `reference` (`does not close #39`
-> `does not reference #39`), edits the PR body, and re-checks. A missing issue is never fixed here.

Env: PR_CLOSING_CHECK_GH  gh wrapper to run (default gh-bot.sh beside this script's parent); it runs with the repo
                          directory as cwd because the bot installation is picked from the cwd's origin.
Exit: 0 matches | 1 mismatch (still, after --fix) | 2 usage | 4 unavailable (no slug, gh failed)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB_DIR))

import wf_lint_base as base  # noqa: E402

KEYWORD = r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)"
GH_TIMEOUT = 90
OK, MISMATCH, USAGE, UNAVAILABLE = 0, 1, 2, 4


class Unavailable(Exception):
    pass


def parse_expect(text: str) -> set[int]:
    if text.strip().lower() in ("", "none"):
        return set()
    try:
        return {int(part) for part in text.split(",")}
    except ValueError as exc:
        raise ValueError(f"--expect must be `none` or comma-separated issue numbers, got `{text}`") from exc


def rewrite_body(body: str, numbers: set[int]) -> str:
    """Replace the closing keyword in front of each `#N` (or owner/repo#N) in `numbers` with `reference`."""
    for number in sorted(numbers):
        body = re.sub(rf"(?i)\b{KEYWORD}\b(\s*:?\s*)((?:[\w.-]+/[\w.-]+)?#{number}\b)", r"reference\1\2", body)
    return body


def lines_mentioning(body: str, number: int) -> list[str]:
    return [line.strip() for line in body.splitlines() if re.search(rf"#{number}\b", line)]


class Checker:
    def __init__(self, root: Path, repo: str) -> None:
        self.root = root
        self.dir = root / repo
        ctx = base.Context(root, Path(os.environ.get("HOME", str(Path.home()))), base.load_config(root))
        self.slug = ctx.seam(repo).token("Repo slug")
        if not self.slug:
            raise Unavailable(f'missing seam key "Repo slug" in {self.dir / "CLAUDE.md"}')
        self.gh = os.environ.get("PR_CLOSING_CHECK_GH") or str(LIB_DIR.parent / "gh-bot.sh")

    def run_gh(self, *args: str) -> str:
        try:
            result = subprocess.run(
                [self.gh, *args], cwd=self.dir, text=True, capture_output=True, check=False, timeout=GH_TIMEOUT
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Unavailable(f"gh could not run: {exc}") from exc
        if result.returncode != 0:
            raise Unavailable(f"gh {' '.join(args[:2])} failed: {(result.stderr or result.stdout).strip()}")
        return result.stdout

    def view(self, pr: str) -> tuple[set[int], str]:
        out = self.run_gh("pr", "view", pr, "--repo", self.slug, "--json", "closingIssuesReferences,body")
        try:
            data = json.loads(out)
            refs = {int(ref["number"]) for ref in data.get("closingIssuesReferences") or []}
            return refs, str(data.get("body") or "")
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise Unavailable(f"unparsable PR view: {exc}") from exc

    def edit_body(self, pr: str, body: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "body.md"
            path.write_text(body)
            self.run_gh("pr", "edit", pr, "--repo", self.slug, "--body-file", str(path))


def report(refs: set[int], expected: set[int], body: str) -> tuple[set[int], set[int]]:
    unexpected, missing = refs - expected, expected - refs
    for number in sorted(unexpected):
        print(f"UNEXPECTED  #{number} will be closed on merge")
        for line in lines_mentioning(body, number):
            print(f"            body: {line}")
    for number in sorted(missing):
        print(f"MISSING     #{number} is expected to close but is not a closing reference")
    return unexpected, missing


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="pr-closing-check.sh", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stronghold", default=os.environ.get("STRONGHOLD_DIR") or str(LIB_DIR.parent.parent.parent))
    parser.add_argument("repo")
    parser.add_argument("pr")
    parser.add_argument("--expect", default="none")
    parser.add_argument("--fix", action="store_true")
    try:
        args = parser.parse_args(argv)
        expected = parse_expect(args.expect)
    except SystemExit as exc:
        return USAGE if exc.code not in (0, None) else OK
    except ValueError as exc:
        print(f"pr-closing-check: {exc}", file=sys.stderr)
        return USAGE
    if not args.pr.isdigit():
        print(f"pr-closing-check: PR must be a number, got `{args.pr}`", file=sys.stderr)
        return USAGE
    try:
        checker = Checker(Path(args.stronghold).resolve(), args.repo)
        refs, body = checker.view(args.pr)
        unexpected, missing = report(refs, expected, body)
        if not unexpected and not missing:
            print(f"ok: PR #{args.pr} closes exactly {sorted(expected) or 'nothing'}")
            return OK
        if args.fix and unexpected:
            fixed = rewrite_body(body, unexpected)
            if fixed == body:
                print("fix: no closing keyword found in the body for those references (linked by hand or by a commit message)", file=sys.stderr)
                return MISMATCH
            checker.edit_body(args.pr, fixed)
            print(f"fix: rewrote the closing keyword for {sorted(unexpected)} and edited the PR body; re-checking")
            refs, body = checker.view(args.pr)
            unexpected, missing = report(refs, expected, body)
            if not unexpected and not missing:
                print(f"ok: PR #{args.pr} closes exactly {sorted(expected) or 'nothing'}")
                return OK
        return MISMATCH
    except Unavailable as exc:
        print(f"pr-closing-check: {exc}", file=sys.stderr)
        return UNAVAILABLE


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
