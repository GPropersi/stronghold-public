#!/usr/bin/env python3
"""Shared plan naming helpers: path-segment validation, title, kind and short_title.

Used by both `plans_ui` (the dashboard) and `plan_activity` (the activity feed) so a plan has one title and one kind
everywhere. Imports only `plan_backfill` and `plan_parse`; it must never import `plans_ui` or `plan_activity`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import plan_backfill  # noqa: E402
import plan_parse  # noqa: E402

SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
NON_PLAN_TYPES = plan_backfill.NON_PLAN_TYPES
MASTER_KINDS = ("master", "orchestrator")
SUFFIX_INFERRED = ("-design", "-progress", "-master")


def valid_segment(value: str) -> bool:
    return bool(SEGMENT_RE.fullmatch(value)) and value not in (".", "..")


def title(lines: list[str], name: str) -> str:
    """First H1 outside fenced code blocks (and outside frontmatter), else the file stem."""
    fence: str | None = None
    for line in lines[plan_parse._split_frontmatter(lines)[1] :]:
        fenced = plan_parse.FENCE_RE.match(line)
        if fence is not None:
            if fenced and fenced.group(1)[0] == fence[0] and len(fenced.group(1)) >= len(fence):
                fence = None
            continue
        if fenced:
            fence = fenced.group(1)
            continue
        if line.startswith("# ") and line[2:].strip():
            return line[2:].strip()
    return name


def kind(path: Path, doc: plan_parse.PlanDoc, lines: list[str]) -> str | None:
    """'plan' | 'master' | 'orchestrator', or None when the file is a filtered non-plan."""
    if doc.type in NON_PLAN_TYPES:
        return None
    if doc.type in MASTER_KINDS:
        return doc.type
    if doc.type is None and path.stem.lower().endswith(SUFFIX_INFERRED):
        inferred = plan_backfill.infer_type(path, lines)
        if inferred in NON_PLAN_TYPES:
            return None
        if inferred in MASTER_KINDS:
            return inferred
    return "plan"


def short_title(doc: plan_parse.PlanDoc) -> str | None:
    return (doc.frontmatter.get("short_title") or "").strip().strip("\"'") or None
