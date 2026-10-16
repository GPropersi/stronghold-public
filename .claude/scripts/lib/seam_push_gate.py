#!/usr/bin/env python3
"""Parser for the `Push gate` seam key, plus the gate's glob matcher (stdlib only).

Seam format (column-0 bullet, then an indented table, mirroring the Commands table):

    - **Push gate:** (free-form prose allowed here)
      | Paths (space-separated globs) | Command                 |
      | ----------------------------- | ----------------------- |
      | `backend/** tests/**`         | `make test-integration` |
      | `docs/** *.md`                | na docs only            |

The first table row is the header and the separator row is skipped. A Command cell is a
backticked command (run by the gate as `bash -c "<command>"` from the repo root) or the literal
`na` followed by a mandatory reason. The gate splits `a && b` into the suites `a` and `b` and runs
each distinct one once per push, so rows may share a suite (see push_gate.split_command). Fenced code blocks are ignored (the root CLAUDE.md carries a
fenced seam template), as is everything outside the `## Claude Config` section.

parse(md_path) -> {"present": bool,
                   "rows": [{"globs": [...], "command": str|None, "na_reason": str|None, "line": int}],
                   "errors": [{"line": int, "message": str}]}
Rows with errors are reported in `errors` and left out of `rows`. CLI: prints parse() as JSON.

glob_match(pattern, path): gitignore-style, anchored at the repo root, over repo-relative
`/`-separated paths. `*` and `?` never cross `/`; `**/` matches zero or more whole directories;
a trailing `/**` matches everything beneath the directory; a pattern without `/` (such as
`*.md`) matches root-level files only (use `**/*.md` for any depth).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

KEY_PREFIX = "- **Push gate:**"
SEPARATOR_CELL = re.compile(r"^:?-+:?$")


def _section_lines(text: str) -> list[tuple[int, str]]:
    """(1-based line number, text) for the unfenced `## Claude Config` section."""
    kept: list[tuple[int, str]] = []
    fence = False
    for number, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if not fence:
            kept.append((number, line))
    section: list[tuple[int, str]] = []
    inside = False
    for number, line in kept:
        if not inside:
            inside = line == "## Claude Config"
            if inside:
                section.append((number, line))
            continue
        if line.startswith("## "):
            break
        section.append((number, line))
    return section


def _split_cells(inner: str) -> list[str]:
    """Split a table row's inner text on `|`, except inside backtick spans (so a Command such as
    `make a || make b` stays one cell). An odd backtick count falls back to a plain split so the
    unbalanced-backticks messages stay precise."""
    if inner.count("`") % 2:
        return [cell.strip() for cell in inner.split("|")]
    cells: list[str] = []
    current: list[str] = []
    inside = False
    for char in inner:
        if char == "`":
            inside = not inside
        if char == "|" and not inside:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    cells.append("".join(current).strip())
    return cells


def _parse_row(line: str) -> tuple[dict | None, str | None]:
    stripped = line.strip()
    cells = _split_cells(stripped[1:-1]) if stripped.endswith("|") else []
    if len(cells) != 2:
        return None, f"expected 2 cells (Paths | Command), found {len(cells) or 'a row without closing |'}"
    glob_cell, command_cell = cells
    if glob_cell.count("`") % 2:
        return None, "unbalanced backticks in the Paths cell"
    globs = glob_cell.replace("`", " ").split()
    if not globs:
        return None, "empty Paths cell"
    if command_cell == "na" or command_cell.startswith("na "):
        reason = command_cell[2:].strip()
        if not reason:
            return None, "`na` row needs a reason after it"
        return {"globs": globs, "command": None, "na_reason": reason}, None
    if command_cell.count("`") % 2:
        return None, "unbalanced backticks in the Command cell"
    if len(command_cell) >= 2 and command_cell.startswith("`") and command_cell.endswith("`"):
        command = command_cell[1:-1].strip()
        if command:
            return {"globs": globs, "command": command, "na_reason": None}, None
        return None, "empty Command cell"
    if not command_cell:
        return None, "empty Command cell"
    return None, "Command must be a backticked command or `na <reason>`"


def parse(md_path: str | Path) -> dict:
    result: dict = {"present": False, "rows": [], "errors": []}
    try:
        text = Path(md_path).read_text()
    except (OSError, UnicodeDecodeError):
        return result
    lines = _section_lines(text)
    start = next((i for i, (_, line) in enumerate(lines) if line.startswith(KEY_PREFIX)), None)
    if start is None:
        return result
    result["present"] = True
    table = 0
    for number, line in lines[start + 1 :]:
        if not (line.startswith((" ", "\t")) and line.strip().startswith("|")):
            break
        table += 1
        if table == 1:
            continue  # header row
        inner = line.strip().strip("|")
        if all(SEPARATOR_CELL.match(cell.strip()) for cell in inner.split("|")):
            continue
        row, error = _parse_row(line)
        if error is not None:
            result["errors"].append({"line": number, "message": error})
        else:
            row["line"] = number
            result["rows"].append(row)
    if table == 0:
        result["errors"].append({"line": lines[start][0], "message": "Push gate has no table"})
    return result


def _regex(pattern: str) -> re.Pattern:
    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("/**", i) and i + 3 == n:
            out.append("/.+")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def glob_match(pattern: str, path: str) -> bool:
    return _regex(pattern).match(path) is not None


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 2
    print(json.dumps(parse(argv[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
