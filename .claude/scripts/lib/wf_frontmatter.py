#!/usr/bin/env python3
"""Plan frontmatter reader (stdlib only).

parse(path) -> dict holding only the keys that are present, from:
  repo branch worktree github_issue github_issue_url   (leading `---` frontmatter only)
  type legacy                                          (legacy -> bool; type stays a raw string)
  finished                                             (bool, from the BODY's `## Status` block)
Absent frontmatter and no Status block gives {}. Missing/unreadable files give {}.
CLI: python3 wf_frontmatter.py <plan.md>  prints the dict as JSON.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

FRONTMATTER_KEYS = (
    "repo",
    "branch",
    "worktree",
    "github_issue",
    "github_issue_url",
    "type",
    "legacy",
)
FINISHED_RE = re.compile(r"^finished:\s*(true|false)\s*$", re.IGNORECASE)


def _split(text: str) -> tuple[list[str], list[str]]:
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                return lines[1:index], lines[index + 1 :]
    return [], lines


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def parse(path: str | Path) -> dict:
    try:
        text = Path(path).read_text()
    except (OSError, UnicodeDecodeError):
        return {}
    front, body = _split(text)
    result: dict = {}
    for line in front:
        if line[:1] in (" ", "\t"):
            continue  # indented lines belong to a nested value, never a top-level key
        key, sep, value = line.partition(":")
        key = key.strip()
        if sep and key in FRONTMATTER_KEYS:
            value = _unquote(value)
            if key == "legacy":
                result[key] = value.lower() == "true"
            else:
                result[key] = value
    in_status = False
    for line in body:
        if line.startswith("## "):
            in_status = line.strip() == "## Status"
            continue
        if in_status:
            found = FINISHED_RE.match(line.strip())
            if found:
                result["finished"] = found.group(1).lower() == "true"
                break
    return result


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 2
    print(json.dumps(parse(argv[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
