#!/usr/bin/env python3
"""Deterministic review-loop helpers for /git-push (stdlib only). Every result is a pure function
of the reviewer files: no model labels, summarizes or judges anything.

    review_tools.py coordinate <dir> --out <file>     merge the 7 reviewer files in <dir>
    review_tools.py scaffold <branch> <n> <base> <verdict> [--now <ts>] [--verdicts k=PASS,...]
    review_tools.py section-number <review-file>      next `## Review N` (1 for a missing file)
    review_tools.py next-round <fix_round>            `proceed` or `blocked`
    review_tools.py issue-title <branch>              `hotfix/login-timeout` -> `Hotfix: Login timeout`

`coordinate` reads `<dir>/<role>.md` (JSON, optionally inside a ``` fence) for each of the 7 roles,
writes the coordinator JSON to --out (required, exit 2 otherwise) and prints only the path.

Grouping (the coordinator's pure function): findings with a file AND an integer line are grouped
per file; sorted by line, a group starts at its first finding (the anchor) and takes every later
finding at most 3 lines below the ANCHOR. Chaining through the last member is NOT applied (A@10,
B@13, C@16 gives {A,B} and {C}). Findings with a null file or line are never grouped. A group with
findings from fewer than 2 distinct reviewers is not multi-source: its findings stay `unique`.
In a multi-source group only each reviewer's first finding (by line) is analysed; that reviewer's
extra findings in the window stay `unique`. Equal but EMPTY suggestions are a conflict, not a dup.
A multi-source group is a `duplicate` iff all normalized suggestions (lowercased, whitespace
collapsed, punctuation kept) are equal, otherwise ALWAYS a `conflict` (fix_type design_decision,
one option per distinct suggestion): a disagreement is never merged silently.
A reviewer is FAIL iff it has a critical or major finding or its file is missing/unusable (then a
single major design_decision finding is synthesized). The overall verdict is FAIL iff any FAIL.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

REVIEWERS = [
    ("safety-security", "Safety & Security"),
    ("correctness", "Correctness"),
    ("simplicity", "Simplicity & Conciseness"),
    ("test-coverage", "Test Coverage"),
    ("completeness", "Completeness & Cleanup"),
    ("consistency", "Consistency & Style"),
    ("integration-risk", "Integration Risk"),
]
ROLE_INDEX = {role: index for index, (role, _) in enumerate(REVIEWERS)}
SEVERITY_RANK = {"minor": 0, "major": 1, "critical": 2}
FIX_TYPES = ("mechanical", "design_decision")
WINDOW = 3
MAX_FIX_ROUNDS = 2
VERDICT_LABELS = ("BLOCKED", "PUSHED WITH MINOR FINDINGS", "RESOLVED — PUSHED")
HEADING_MECHANICAL = "To-Do: Mechanical Fixes"
HEADING_DESIGN = "To-Do: Design Decisions"
LABEL_WORDS = 4


class Unusable(Exception):
    pass


def normalize(text: str) -> str:
    return " ".join(str(text).lower().split())


def load_reviewer(path: Path | None) -> tuple[list[dict], str]:
    """(findings, summary) of one reviewer file; raises Unusable(reason) when unusable."""
    if path is None or not Path(path).is_file():
        raise Unusable("output file missing or unreadable")
    try:
        text = Path(path).read_text()
    except (OSError, UnicodeDecodeError):
        raise Unusable("output file missing or unreadable") from None
    fenced = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    try:
        data = json.loads(fenced.group(1) if fenced else text)
    except ValueError:
        raise Unusable("output is not valid JSON") from None
    findings = data.get("findings") if isinstance(data, dict) else None
    if not isinstance(findings, list) or not all(isinstance(f, dict) for f in findings):
        raise Unusable("output does not match the reviewer schema")
    summary = data.get("summary")
    clean = []
    for finding in findings:
        severity = finding.get("severity")
        fix_type = finding.get("fix_type")
        line = finding.get("line")
        file = finding.get("file")
        clean.append(
            {
                "severity": severity if severity in SEVERITY_RANK else "major",
                "fix_type": fix_type if fix_type in FIX_TYPES else "design_decision",
                "file": file if isinstance(file, str) and file else None,
                "line": line if isinstance(line, int) and not isinstance(line, bool) else None,
                "description": str(finding.get("description", "")),
                "suggestion": str(finding.get("suggestion", "")),
            }
        )
    return clean, summary if isinstance(summary, str) else ""


def option_label(suggestion: str) -> str:
    words = re.sub(r"[.,;:!?]", "", suggestion).split()
    return " ".join(words[:LABEL_WORDS])


def highest(members: list[dict]) -> dict:
    return max(members, key=lambda f: (SEVERITY_RANK[f["severity"]], -ROLE_INDEX[f["source"]]))


def merge_group(members: list[dict]) -> dict:
    sources = sorted({m["source"] for m in members}, key=ROLE_INDEX.get)
    top = highest(members)
    base = {
        "severity": top["severity"],
        "file": top["file"],
        "line": min(m["line"] for m in members),
        "sources": sources,
    }
    suggestions = {normalize(m["suggestion"]) for m in members}
    # An empty suggestion proves nothing about "the same fix": never merge on it.
    if len(suggestions) == 1 and "" not in suggestions:
        design = any(m["fix_type"] == "design_decision" for m in members)
        return {
            **base,
            "fix_type": "design_decision" if design else "mechanical",
            "classification": "duplicate",
            "description": top["description"],
            "suggestion": top["suggestion"],
        }
    options = []
    seen: dict[str, dict] = {}
    for member in sorted(members, key=lambda m: ROLE_INDEX[m["source"]]):
        key = normalize(member["suggestion"])
        if key in seen:
            entry = seen[key]
            if member["source"] not in entry["_sources"]:
                entry["_sources"].append(member["source"])
            continue
        entry = {"_sources": [member["source"]], "suggestion": member["suggestion"]}
        seen[key] = entry
        options.append(entry)
    rendered = [
        {
            "source": ", ".join(e["_sources"]),
            "label": option_label(e["suggestion"]),
            "suggestion": e["suggestion"],
        }
        for e in options
    ]
    positions = "; ".join(
        f"{m['source']}: {m['description']}"
        for m in sorted(members, key=lambda m: (ROLE_INDEX[m["source"]], m["line"]))
    )
    return {
        **base,
        "fix_type": "design_decision",
        "classification": "conflict",
        "description": f"Reviewers disagree at {top['file']}:{base['line']} ({positions})",
        "suggestion": "Choose one of the options.",
        "options": rendered,
    }


def unique_finding(member: dict) -> dict:
    return {
        "severity": member["severity"],
        "fix_type": member["fix_type"],
        "classification": "unique",
        "sources": [member["source"]],
        "file": member["file"],
        "line": member["line"],
        "description": member["description"],
        "suggestion": member["suggestion"],
    }


def group_findings(flat: list[dict]) -> list[list[dict]]:
    """Anchor-window groups of the locatable findings; the rest are singleton groups."""
    groups: list[list[dict]] = []
    by_file: dict[str, list[dict]] = {}
    for finding in flat:
        if finding["file"] is None or finding["line"] is None:
            groups.append([finding])
        else:
            by_file.setdefault(finding["file"], []).append(finding)
    for file in sorted(by_file):
        ordered = sorted(by_file[file], key=lambda f: (f["line"], ROLE_INDEX[f["source"]]))
        current: list[dict] = []
        for finding in ordered:
            if current and finding["line"] - current[0]["line"] <= WINDOW:
                current.append(finding)
            else:
                if current:
                    groups.append(current)
                current = [finding]
        if current:
            groups.append(current)
    return groups


def sort_key(finding: dict) -> tuple:
    return (
        finding["file"] is None,
        finding["file"] or "",
        finding["line"] is None,
        finding["line"] or 0,
        ROLE_INDEX[finding["sources"][0]],
        finding["description"],
    )


def coordinate(files: dict) -> dict:
    """Merge the reviewer outputs. `files` maps a role slug to its file path (or None)."""
    verdicts: dict[str, str] = {}
    summaries: dict[str, str] = {}
    flat: list[dict] = []
    for role, _ in REVIEWERS:
        try:
            findings, summary = load_reviewer(files.get(role))
        except Unusable as err:
            reason = str(err)
            verdicts[role] = "FAIL"
            summaries[role] = f"Reviewer output unusable: {reason}"
            flat.append(
                {
                    "severity": "major",
                    "fix_type": "design_decision",
                    "file": None,
                    "line": None,
                    "description": f"Reviewer '{role}' returned no usable output ({reason}); "
                    "treat the review as failed and re-run it.",
                    "suggestion": f"Re-run the {role} reviewer.",
                    "source": role,
                }
            )
            continue
        verdicts[role] = (
            "FAIL" if any(SEVERITY_RANK[f["severity"]] >= SEVERITY_RANK["major"] for f in findings) else "PASS"
        )
        summaries[role] = summary
        flat.extend({**f, "source": role} for f in findings)

    merged = []
    for members in group_findings(flat):
        # One finding per reviewer joins the cross-reviewer analysis (its first by line); a
        # reviewer's extra findings in the window stay unique, never blended into a merge.
        first: dict[str, dict] = {}
        extras: list[dict] = []
        for member in members:
            if member["source"] in first:
                extras.append(member)
            else:
                first[member["source"]] = member
        if len(first) < 2:
            merged.extend(unique_finding(m) for m in members)
        else:
            merged.append(merge_group(list(first.values())))
            merged.extend(unique_finding(m) for m in extras)
    merged.sort(key=sort_key)
    return {
        "verdict": "FAIL" if "FAIL" in verdicts.values() else "PASS",
        "reviewer_verdicts": verdicts,
        "summaries": summaries,
        "findings": merged,
    }


def coordinate_dir(directory: Path) -> dict:
    directory = Path(directory)
    return coordinate({role: directory / f"{role}.md" for role, _ in REVIEWERS})


def partition(findings: list[dict]) -> dict:
    return {
        "mechanical_fixes": [f for f in findings if f.get("fix_type") == "mechanical"],
        "design_decisions": [f for f in findings if f.get("fix_type") == "design_decision"],
    }


def next_round(fix_round: int) -> str:
    """`proceed` while fix_round <= MAX_FIX_ROUNDS, else `blocked` (BLOCKED = failures remain
    after 2 fix rounds; resolves the old `<= 2` off-by-one that allowed a third pass)."""
    return "proceed" if fix_round <= MAX_FIX_ROUNDS else "blocked"


def review_section_number(path: Path | str) -> int:
    try:
        text = Path(path).read_text()
    except (OSError, UnicodeDecodeError):
        return 1
    numbers = [int(n) for n in re.findall(r"^## Review (\d+)\s*$", text, re.MULTILINE)]
    return max(numbers) + 1 if numbers else 1


def scaffold(
    branch: str,
    n: int,
    base: str,
    verdict: str,
    now: str | None = None,
    verdicts: dict | None = None,
    mechanical: bool = True,
    design: bool = True,
) -> str:
    """Header (only for review 1) and section skeleton with the exact headings the skills count."""
    if verdict not in VERDICT_LABELS:
        raise ValueError(f"verdict must be one of {VERDICT_LABELS}")
    stamp = now or datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    verdicts = verdicts or {}
    parts = []
    if n == 1:
        parts.append(f"# Push Review: {branch}\n")
    parts.append(
        f"## Review {n}\nGenerated: {stamp}\nComparison: {base}...HEAD\nVerdict: **{verdict}**\n\n"
        "### Results by Reviewer\n"
    )
    for index, (role, title) in enumerate(REVIEWERS, start=1):
        parts.append(f"#### {index}. {title} — {verdicts.get(role, 'PENDING')}\n")
    if mechanical:
        parts.append(f"### {HEADING_MECHANICAL}\n")
    if design:
        parts.append(f"### {HEADING_DESIGN}\n")
    return "\n".join(parts)


def issue_title(branch: str) -> str:
    segments = [s for s in branch.split("/") if s]
    if not segments:
        return ""

    def words(text: str) -> str:
        return " ".join(w for w in re.split(r"[-_\s]+", text.lower()) if w)

    if len(segments) == 1:
        text = words(segments[0])
        return text[:1].upper() + text[1:]
    prefix = words(segments[0])
    rest = words(" ".join(segments[1:]))
    return f"{prefix[:1].upper() + prefix[1:]}: {rest[:1].upper() + rest[1:]}"


def issue_body(subjects: list[str]) -> str:
    bullets = "".join(f"- {subject}\n" for subject in subjects)
    return f"## Why\n\n{bullets}"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("coordinate")
    cmd.add_argument("directory")
    cmd.add_argument("--out", required=True)
    cmd = sub.add_parser("scaffold")
    cmd.add_argument("branch")
    cmd.add_argument("n", type=int)
    cmd.add_argument("base")
    cmd.add_argument("verdict")
    cmd.add_argument("--now")
    cmd.add_argument("--verdicts", default="")
    cmd = sub.add_parser("section-number")
    cmd.add_argument("path")
    cmd = sub.add_parser("next-round")
    cmd.add_argument("fix_round", type=int)
    cmd = sub.add_parser("issue-title")
    cmd.add_argument("branch")
    args = parser.parse_args(argv)

    if args.command == "coordinate":
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(coordinate_dir(Path(args.directory)), indent=2) + "\n")
        print(out)
    elif args.command == "scaffold":
        pairs = dict(p.split("=", 1) for p in args.verdicts.split(",") if "=" in p)
        try:
            print(scaffold(args.branch, args.n, args.base, args.verdict, args.now, pairs))
        except ValueError as err:
            print(f"review_tools.py: {err}", file=sys.stderr)
            return 2
    elif args.command == "section-number":
        print(review_section_number(args.path))
    elif args.command == "next-round":
        print(next_round(args.fix_round))
    else:
        print(issue_title(args.branch))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
