#!/usr/bin/env python3
"""Shared plan parser (stdlib only) used by plan lint, lifecycle code and the plans dashboard.

parse_plan(path) -> PlanDoc: frontmatter, doc type, issue link, Status-only `finished:`, `### N.`
steps with their checkboxes and master fields. Step regex, step end rules and box ranges follow
plan-status.sh (a step ends at the next step heading or any `## ` heading); additionally fenced
code blocks are skipped and `## Progress Tracking` boxes are excluded.
resolve_link(plan_path, link) -> Resolved: ok | moved (other lifecycle state) | dangling.
CLI: python3 plan_parse.py <plan.md>  prints a JSON summary.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

STEP_RE = re.compile(r"^###[ \t]+(?:Step[ \t]+)?([0-9]+)[.:)][ \t]+(.*)$")
STEP_PREFIX_RE = re.compile(r"^###[ \t]+(Step[ \t]+)?[0-9]+[.:)][ \t]")
H2_RE = re.compile(r"^##[ \t]")
BOX_RE = re.compile(r"^[ \t]*- \[([ xX~])\][ \t]*(.*)$")
FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):(.*)$")
FINISHED_RE = re.compile(r"^finished:\s*(true|false)\s*$", re.IGNORECASE)
SUBPLAN_RE = re.compile(r"^\*\*Sub-plan:\*\*[ \t]*(.*?)[ \t]*$")
BRANCH_RE = re.compile(r"^\*\*Branch:\*\*[ \t]*(.*?)[ \t]*$")
STATUS_RE = re.compile(r"^\*\*Status:\*\*[ \t]*(.*?)[ \t]*$")
MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
MANUAL_TAG_RE = re.compile(r"^\(manual,[ \t]*post-merge\)")
POST_TAG_RE = re.compile(r"^\(post-merge\)")
STATES = ("open", "completed")


@dataclass
class Box:
    state: str  # done | open | deferred | post-merge | manual-post-merge
    text: str
    line: int  # 1-based line number in the file
    checkbox: str = " "  # raw marker character: ' ', 'x', 'X' or '~'


@dataclass
class Step:
    n: int
    title: str
    start: int
    end: int
    boxes: list[Box] = field(default_factory=list)
    sub_plan_link: str | None = None  # link target, 'none', or None when no Sub-plan line
    branch: str | None = None
    status: str | None = None


@dataclass
class PlanDoc:
    path: str
    frontmatter: dict
    type: str | None
    effective_type: str
    legacy: bool
    finished: bool | None
    finished_in_frontmatter: bool
    github_issue: int | str | None  # int | 'none' | 'invalid' | None (absent)
    github_issue_raw: str | None
    github_issue_url: str | None
    steps: list[Step]
    stray_boxes: list[Box]
    progress_tracking_present: bool
    h1_count: int


@dataclass
class Resolved:
    kind: str  # ok | moved | dangling
    path: Path | None


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def _classify(marker: str, text: str) -> str:
    if marker == "~":
        return "deferred"
    if marker in ("x", "X"):
        return "done"
    if MANUAL_TAG_RE.match(text):
        return "manual-post-merge"
    if POST_TAG_RE.match(text):
        return "post-merge"
    return "open"


def _issue(raw: str | None) -> int | str | None:
    if raw is None:
        return None
    if raw.isascii() and raw.isdigit():
        return int(raw)
    if raw == "none":
        return "none"
    return "invalid"


def _split_frontmatter(lines: list[str]) -> tuple[dict, int]:
    """Return (frontmatter dict of raw top-level scalars, index of the first body line)."""
    if lines and lines[0].rstrip() == "---":
        for index in range(1, len(lines)):
            if lines[index].rstrip() == "---":
                front: dict = {}
                for line in lines[1:index]:
                    if line[:1] in (" ", "\t"):
                        continue
                    found = KEY_RE.match(line)
                    if found:
                        front[found.group(1)] = _unquote(found.group(2))
                return front, index + 1
    return {}, 0


def parse_plan(path: str | Path) -> PlanDoc:
    try:
        text = Path(path).read_text()
    except (OSError, UnicodeDecodeError):
        text = ""
    lines = text.splitlines()
    front, body_start = _split_frontmatter(lines)

    raw_type = front.get("type") or None
    raw_issue = front["github_issue"] if "github_issue" in front else None
    steps: list[Step] = []
    stray: list[Box] = []
    current: Step | None = None
    section = ""  # lowercased text of the current `## ` heading
    progress_seen = False
    finished: bool | None = None
    h1_count = 0
    fence: str | None = None

    def close(end_line: int) -> None:
        nonlocal current
        if current is not None:
            current.end = end_line
            steps.append(current)
            current = None

    for index in range(body_start, len(lines)):
        number = index + 1
        line = lines[index]
        fenced = FENCE_RE.match(line)
        if fence is not None:
            if fenced and fenced.group(1)[0] == fence[0] and len(fenced.group(1)) >= len(fence):
                fence = None
            continue
        if fenced:
            fence = fenced.group(1)
            continue
        if line.startswith("# "):
            h1_count += 1
        in_progress = section == "progress tracking"
        if H2_RE.match(line):
            close(number - 1)
            section = line[2:].strip().lower()
            if section == "progress tracking":
                progress_seen = True
            continue
        if not in_progress:
            step_match = STEP_RE.match(line) if STEP_PREFIX_RE.match(line) else None
            if step_match:
                close(number - 1)
                current = Step(int(step_match.group(1)), step_match.group(2).strip(), number, number)
                continue
        if section == "status" and finished is None:
            found = FINISHED_RE.match(line.strip())
            if found:
                finished = found.group(1).lower() == "true"
        box = BOX_RE.match(line)
        if box and not in_progress:
            marker, content = box.group(1), box.group(2)
            item = Box(_classify(marker, content), content, number, marker)
            if current is not None:
                current.boxes.append(item)
            else:
                stray.append(item)
            continue
        if current is not None:
            _master_field(current, line)
    close(len(lines))

    return PlanDoc(
        path=str(path),
        frontmatter=front,
        type=raw_type,
        effective_type=raw_type or "plan",
        legacy=front.get("legacy", "").strip().lower() == "true",
        finished=finished,
        finished_in_frontmatter="finished" in front,
        github_issue=_issue(raw_issue),
        github_issue_raw=raw_issue,
        github_issue_url=front.get("github_issue_url") or None,
        steps=steps,
        stray_boxes=stray,
        progress_tracking_present=progress_seen,
        h1_count=h1_count,
    )


def _master_field(step: Step, line: str) -> None:
    found = SUBPLAN_RE.match(line)
    if found and step.sub_plan_link is None:
        value = found.group(1)
        link = MD_LINK_RE.search(value)
        if link:
            step.sub_plan_link = link.group(1)
        elif value.strip("`*_ ").lower() == "none":
            step.sub_plan_link = "none"
        return
    found = BRANCH_RE.match(line)
    if found and step.branch is None:
        step.branch = found.group(1).strip("` ")
        return
    found = STATUS_RE.match(line)
    if found and step.status is None:
        step.status = found.group(1)


def resolve_link(plan_path: str | Path, link: str) -> Resolved:
    """Resolve a sub-plan link relative to the master's directory."""
    target = link.strip().strip("<>").split("#", 1)[0]
    if not target:
        return Resolved("dangling", None)
    if SCHEME_RE.match(target):
        return Resolved("ok", None)
    resolved = Path(os.path.normpath(Path(plan_path).resolve().parent / target))
    if resolved.exists():
        return Resolved("ok", resolved)
    parts = resolved.parts
    # .../<bucket>/<state>/<topic>/<name>.md
    if len(parts) >= 4 and parts[-3] in STATES:
        other = STATES[1] if parts[-3] == STATES[0] else STATES[0]
        moved = Path(*parts[:-3], other, *parts[-2:])
        if moved.exists():
            return Resolved("moved", moved)
    return Resolved("dangling", resolved)


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 2
    print(json.dumps(asdict(parse_plan(argv[1])), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
