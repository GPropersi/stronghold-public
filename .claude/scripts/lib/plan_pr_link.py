#!/usr/bin/env python3
"""Record a pull-request URL in a plan's frontmatter so the plans dashboard can show it.

Usage: plan_pr_link.py add <plan-file> <pr-url>

Adds the URL to the `pr_urls:` frontmatter key (space-separated, in the order linked). Idempotent: a URL
already present changes nothing. Only https://github.com/<owner>/<repo>/pull/<n> URLs are accepted.
Exit: 0 ok (prints `added` or `already linked`) | 2 usage or bad URL | 3 missing file or no frontmatter.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

KEY = "pr_urls"
PR_URL_RE = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[0-9]+$")


def add_pr(path: Path, url: str) -> bool:
    """Return True when the file changed. Raises ValueError for a bad URL, FileNotFoundError or LookupError."""
    if not PR_URL_RE.fullmatch(url):
        raise ValueError(f"not a GitHub pull request URL: {url!r}")
    lines = path.read_text().split("\n")
    if not lines or lines[0].rstrip() != "---":
        raise LookupError("plan has no YAML frontmatter")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].rstrip() == "---")
    except StopIteration:
        raise LookupError("plan frontmatter is not closed") from None
    for index in range(1, end):
        if lines[index].startswith(f"{KEY}:"):
            current = lines[index][len(KEY) + 1 :].strip().strip("\"'").split()
            if url in current:
                return False
            lines[index] = f"{KEY}: " + " ".join([*current, url])
            break
    else:
        after = max((i for i in range(1, end) if lines[i].startswith("branch:")), default=end - 1)
        lines.insert(after + 1, f"{KEY}: {url}")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".plan-pr-link-")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write("\n".join(lines))
        os.chmod(tmp, path.stat().st_mode & 0o777)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise
    return True


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[1] != "add":
        print(__doc__, file=sys.stderr)
        return 2
    try:
        changed = add_pr(Path(argv[2]), argv[3])
    except ValueError as err:
        print(f"plan-pr-link: {err}", file=sys.stderr)
        return 2
    except (FileNotFoundError, LookupError) as err:
        print(f"plan-pr-link: {err}", file=sys.stderr)
        return 3
    print("added" if changed else "already linked")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
