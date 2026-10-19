#!/usr/bin/env python3
"""Core of `plan-lifecycle.sh backfill`: idempotent migration of the plans store to the plan schema (stdlib only).

    plan-lifecycle.sh backfill [--dry-run] [--only <rule>] [--force]

Rules run in this order over every topic-root `*.md` of every bucket; each is computed in memory first so a later
rule sees the result of an earlier one:
  (a) type          insert `type:` (inferred from the filename) only where the key is absent
  (b) finished      move frontmatter `finished:` into `## Status`; supply a missing one (true in completed/, false in open/)
  (c) links         rewrite `moved` sub-plan links to the correct relative path
  (d) master steps  normalise legacy `**Status:**` text, add missing `**Sub-plan:** none` / `**Status:**` lines
  (e) ticks         tick unmarked body boxes in completed/ topics whose owning sub-plan (or plan itself) is finished
  (f) legacy        `legacy: true` on plans with zero parseable steps, only where `legacy:` is absent
  (g) repo/issue    insert `repo: <bucket>` and `github_issue: none` only where the key is absent
Existing `github_issue*`, `repo:`, `branch:`, `worktree:`, `type:` and `legacy:` values are never modified, and fenced
code is never touched. A file with uncommitted or untracked changes (or when git cannot answer) is skipped, but only
when a rule would change it, unless --force. Nothing is written under --dry-run.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import plan_parse
import wf_lint_base as base

STATES = ("open", "completed")
NON_PLAN_TYPES = frozenset({"design", "progress", "prompt", "runbook", "handoff", "spec"})
RULES = ("a", "b", "c", "d", "e", "f", "g")
LABELS = {
    "a": "type",
    "b": "finished",
    "c": "links",
    "d": "master steps",
    "e": "ticks",
    "f": "legacy",
    "g": "repo/issue",
}
VERBS = {
    "a": ("add type to", "added type to"),
    "b": ("set finished in", "set finished in"),
    "c": ("relink", "relinked"),
    "d": ("fix master steps in", "fixed master steps in"),
    "e": ("tick boxes in", "ticked boxes in"),
    "f": ("mark legacy", "marked legacy"),
    "g": ("add repo/issue to", "added repo/issue to"),
}
FINISHED_RE = re.compile(r"^finished:\s*(true|false)\s*$", re.IGNORECASE)
VOCAB_RE = re.compile(r"^(todo|in-progress|done|merged|moved)\b")
LEAD_RE = re.compile(r"^[^A-Za-z0-9]+")
STATUS_MAP = (
    ("not started", "todo"),
    ("in progress", "in-progress"),
    ("in-progress", "in-progress"),
    ("todo", "todo"),
    ("completed", "done"),
    ("done", "done"),
    ("merged", "merged"),
    ("moved", "moved"),
)
UNTICKED_RE = re.compile(r"^([ \t]*- \[) \]")


# --- line helpers -------------------------------------------------------------------------------


def fm_range(lines: list[str]) -> tuple[int, int] | None:
    """(index of the opening `---`, index of the closing `---`) of the leading frontmatter, else None."""
    if lines and lines[0].rstrip() == "---":
        for index in range(1, len(lines)):
            if lines[index].rstrip() == "---":
                return 0, index
    return None


def fm_key_index(lines: list[str], key: str) -> int | None:
    found = fm_range(lines)
    if found is None:
        return None
    for index in range(1, found[1]):
        if lines[index].startswith(f"{key}:"):
            return index
    return None


def fm_get(lines: list[str], key: str) -> str | None:
    index = fm_key_index(lines, key)
    if index is None:
        return None
    return plan_parse._unquote(lines[index].partition(":")[2])


def fm_add(lines: list[str], key: str, value: str) -> None:
    found = fm_range(lines)
    if found is None:
        lines[0:0] = ["---", f"{key}: {value}", "---"]
    else:
        lines.insert(found[1], f"{key}: {value}")


@dataclass
class Span:
    n: int
    start: int  # heading line index
    end: int  # exclusive


@dataclass
class Scan:
    steps: list[Span]
    live: list[bool]  # outside fenced blocks (fence delimiters excluded)
    counted: list[bool]  # live and outside `## Progress Tracking`


def scan(lines: list[str]) -> Scan:
    found = fm_range(lines)
    begin = found[1] + 1 if found else 0
    live = [False] * len(lines)
    counted = [False] * len(lines)
    steps: list[Span] = []
    current: list[int] | None = None
    fence: str | None = None
    section = ""

    def close(end: int) -> None:
        nonlocal current
        if current is not None:
            steps.append(Span(current[0], current[1], end))
            current = None

    for index in range(begin, len(lines)):
        line = lines[index]
        fenced = plan_parse.FENCE_RE.match(line)
        if fence is not None:
            if fenced and fenced.group(1)[0] == fence[0] and len(fenced.group(1)) >= len(fence):
                fence = None
            continue
        if fenced:
            fence = fenced.group(1)
            continue
        live[index] = True
        in_progress = section == "progress tracking"
        if plan_parse.H2_RE.match(line):
            close(index)
            section = line[2:].strip().lower()
            continue
        counted[index] = not in_progress
        if not in_progress and plan_parse.STEP_PREFIX_RE.match(line):
            step = plan_parse.STEP_RE.match(line)
            if step:
                close(index)
                current = [int(step.group(1)), index]
    close(len(lines))
    return Scan(steps, live, counted)


def find_field(lines: list[str], sc: Scan, step: Span, regex: re.Pattern) -> int | None:
    for index in range(step.start + 1, step.end):
        if sc.live[index] and regex.match(lines[index]):
            return index
    return None


def status_heading(lines: list[str], sc: Scan) -> int | None:
    found = fm_range(lines)
    for index in range((found[1] + 1) if found else 0, len(lines)):
        if sc.live[index] and lines[index].strip() == "## Status":
            return index
    return None


def status_lines(lines: list[str], sc: Scan) -> list[int]:
    """Indices of the live lines inside the first `## Status` section."""
    heading = status_heading(lines, sc)
    if heading is None:
        return []
    out = []
    for index in range(heading + 1, len(lines)):
        if sc.live[index] and plan_parse.H2_RE.match(lines[index]):
            break
        if sc.live[index]:
            out.append(index)
    return out


def finished_of(lines: list[str]) -> bool | None:
    sc = scan(lines)
    for index in status_lines(lines, sc):
        found = FINISHED_RE.match(lines[index].strip())
        if found:
            return found.group(1).lower() == "true"
    return None


def append_block(lines: list[str], block: list[str]) -> None:
    if lines and lines[-1] == "":
        lines.pop()
    if lines and lines[-1].strip() != "":
        lines.append("")
    lines.extend(block)
    lines.append("")


def infer_type(path: Path, lines: list[str]) -> str:
    stem = path.stem.lower()
    if stem.endswith("-master"):
        sc = scan(lines)
        if any(find_field(lines, sc, step, plan_parse.BRANCH_RE) is not None for step in sc.steps):
            return "master"
        in_step = {i for step in sc.steps for i in range(step.start, step.end)}
        top = [i for i in range(len(lines)) if sc.live[i] and i not in in_step and plan_parse.BRANCH_RE.match(lines[i])]
        return "orchestrator" if len(top) == 1 else "master"
    if stem.endswith("-design"):
        return "design"
    if stem.endswith("-progress"):
        return "progress"
    if "execution-prompt" in stem:
        return "prompt"
    if "runbook" in stem or "pr-notes" in stem:
        return "runbook"
    if stem.startswith("handoff"):
        return "handoff"
    if "spec" in stem:
        return "spec"
    return "plan"


class Doc:
    def __init__(self, path: Path, rel: str, bucket: str, state: str, text: str) -> None:
        self.path = path
        self.rel = rel
        self.bucket = bucket
        self.state = state
        self.original = text
        self.lines = text.split("\n")
        self.hit: set[str] = set()

    def type(self) -> str:
        return fm_get(self.lines, "type") or infer_type(self.path, self.lines)

    def is_plan(self) -> bool:
        return self.type() not in NON_PLAN_TYPES

    def text(self) -> str:
        return "\n".join(self.lines)


class Backfill:
    def __init__(self, store, rules: tuple[str, ...], dry_run: bool, force: bool) -> None:
        self.store = store
        self.rules = rules
        self.dry_run = dry_run
        self.force = force
        self.docs: list[Doc] = []
        self.by_path: dict[Path, Doc] = {}
        self.reports: list[tuple[str, str, int, str]] = []

    # --- loading -----------------------------------------------------------------------------

    def load(self) -> None:
        for bucket in self.store.buckets(None):
            for state in STATES:
                for topic in self.store.topics(bucket, state):
                    for path in self.store.topic_markdown(topic):
                        try:
                            text = path.read_text(encoding="utf-8")
                        except (OSError, UnicodeDecodeError):
                            continue
                        doc = Doc(path, self.rel(path), bucket, state, text)
                        self.docs.append(doc)
                        self.by_path[path.resolve()] = doc

    def rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.store.root))
        except ValueError:
            return str(path)

    def finished_of_path(self, path: Path) -> bool | None:
        doc = self.by_path.get(path.resolve())
        if doc is not None:
            return finished_of(doc.lines)
        try:
            return finished_of(path.read_text(encoding="utf-8").split("\n"))
        except (OSError, UnicodeDecodeError):
            return None

    # --- rules -------------------------------------------------------------------------------

    def rule_a(self, doc: Doc) -> bool:
        if fm_key_index(doc.lines, "type") is not None:
            return False
        fm_add(doc.lines, "type", infer_type(doc.path, doc.lines))
        return True

    def rule_b(self, doc: Doc) -> bool:
        changed = False
        sc = scan(doc.lines)
        in_front = fm_get(doc.lines, "finished")
        value = in_front.strip().lower() if in_front is not None else None
        has_status_line = any(re.match(r"^finished:", doc.lines[i]) for i in status_lines(doc.lines, sc))
        if value in ("true", "false"):
            del doc.lines[fm_key_index(doc.lines, "finished")]
            changed = True
            if not has_status_line:
                self.add_status(doc, value)
        elif in_front is None and not has_status_line and doc.is_plan():
            self.add_status(doc, "true" if doc.state == "completed" else "false")
            changed = True
        return changed

    def add_status(self, doc: Doc, value: str) -> None:
        heading = status_heading(doc.lines, scan(doc.lines))
        if heading is None:
            append_block(doc.lines, ["## Status", f"finished: {value}"])
        else:
            doc.lines.insert(heading + 1, f"finished: {value}")

    def rule_c(self, doc: Doc) -> bool:
        if not doc.is_plan():
            return False
        sc = scan(doc.lines)
        changed = False
        for step in sc.steps:
            index = find_field(doc.lines, sc, step, plan_parse.SUBPLAN_RE)
            if index is None:
                continue
            link = plan_parse.MD_LINK_RE.search(doc.lines[index])
            if not link:
                continue
            found = plan_parse.resolve_link(doc.path, link.group(1))
            if found.kind != "moved" or found.path is None:
                continue
            raw = link.group(1).strip().strip("<>")
            _, hash_, anchor = raw.partition("#")
            new = os.path.relpath(found.path, doc.path.resolve().parent) + hash_ + anchor
            if link.group(1).startswith("<") and link.group(1).endswith(">"):
                new = f"<{new}>"
            doc.lines[index] = doc.lines[index][: link.start(1)] + new + doc.lines[index][link.end(1) :]
            changed = True
        return changed

    def link_target(self, doc: Doc, value: str | None) -> tuple[str, Path | None]:
        """('none', None) | ('sub', file) | ('unresolved', None) for a step's **Sub-plan:** value."""
        if value is None:
            return "none", None
        link = plan_parse.MD_LINK_RE.search(value)
        if not link:
            return ("none", None) if value.strip("`*_ ").lower() == "none" else ("unresolved", None)
        found = plan_parse.resolve_link(doc.path, link.group(1))
        if found.kind in ("ok", "moved") and found.path is not None and found.path.is_file():
            return "sub", found.path
        return "unresolved", None

    def rule_d(self, doc: Doc) -> bool:
        if doc.type() != "master":
            return False
        sc = scan(doc.lines)
        own_finished = finished_of(doc.lines)
        changed = False
        for step in reversed(sc.steps):
            lines = doc.lines
            link_i = find_field(lines, sc, step, plan_parse.SUBPLAN_RE)
            branch_i = find_field(lines, sc, step, plan_parse.BRANCH_RE)
            status_i = find_field(lines, sc, step, plan_parse.STATUS_RE)
            link_value = plan_parse.SUBPLAN_RE.match(lines[link_i]).group(1) if link_i is not None else None
            kind, target = self.link_target(doc, link_value)
            sub_finished = self.finished_of_path(target) if target is not None else None
            if branch_i is None:
                self.reports.append(("missing Branch", doc.rel, step.n, ""))
            if kind == "sub" and doc.state == "completed" and sub_finished is not True:
                self.reports.append(("completed-unfinished", doc.rel, step.n, ""))
            if status_i is not None:
                text = plan_parse.STATUS_RE.match(lines[status_i]).group(1)
                if not VOCAB_RE.match(text):
                    mapped = self.map_status(text)
                    if mapped is None:
                        self.reports.append(("unmapped Status", doc.rel, step.n, text))
                    else:
                        lines[status_i] = f"**Status:** {mapped} — {text}"
                        changed = True
            else:
                if kind == "sub":
                    value = "done" if sub_finished is True else "todo"
                elif kind == "unresolved":
                    value = "done" if doc.state == "completed" else "todo"
                else:
                    value = "done" if doc.state == "completed" and own_finished is True else "todo"
                after = branch_i if branch_i is not None else (link_i if link_i is not None else step.start)
                lines.insert(after + 1, f"**Status:** {value}")
                changed = True
            if link_i is None:
                lines.insert(step.start + 1, "**Sub-plan:** none")
                changed = True
        return changed

    @staticmethod
    def map_status(text: str) -> str | None:
        low = LEAD_RE.sub("", text).lower()
        for prefix, value in STATUS_MAP:
            if re.match(rf"{re.escape(prefix)}(?![A-Za-z0-9])", low):
                return value
        return None

    def rule_e(self, doc: Doc) -> bool:
        if doc.state != "completed" or not doc.is_plan():
            return False
        sc = scan(doc.lines)
        own_finished = finished_of(doc.lines) is True
        is_master = doc.type() == "master"
        changed = False
        for step in sc.steps:
            owner_finished = own_finished
            if is_master:
                link_i = find_field(doc.lines, sc, step, plan_parse.SUBPLAN_RE)
                value = plan_parse.SUBPLAN_RE.match(doc.lines[link_i]).group(1) if link_i is not None else None
                kind, target = self.link_target(doc, value)
                if kind == "sub":
                    owner_finished = self.finished_of_path(target) is True
                elif kind == "unresolved":
                    owner_finished = False
            if not owner_finished:
                continue
            for index in range(step.start + 1, step.end):
                if not sc.counted[index]:
                    continue
                box = plan_parse.BOX_RE.match(doc.lines[index])
                if not box or box.group(1) != " ":
                    continue
                if plan_parse.MANUAL_TAG_RE.match(box.group(2)) or plan_parse.POST_TAG_RE.match(box.group(2)):
                    continue
                doc.lines[index] = UNTICKED_RE.sub(r"\1x]", doc.lines[index], count=1)
                changed = True
        return changed

    def rule_f(self, doc: Doc) -> bool:
        if not doc.is_plan() or fm_key_index(doc.lines, "legacy") is not None or scan(doc.lines).steps:
            return False
        fm_add(doc.lines, "legacy", "true")
        return True

    def rule_g(self, doc: Doc) -> bool:
        if not doc.is_plan():
            return False
        changed = False
        for key, value in (("repo", doc.bucket), ("github_issue", "none")):
            if fm_key_index(doc.lines, key) is None:
                fm_add(doc.lines, key, value)
                changed = True
        return changed

    # --- run ---------------------------------------------------------------------------------

    def tree_state(self, path: Path) -> str | None:
        code, out = base.git(self.store.root, "status", "--porcelain", "--", str(path))
        if code != 0:
            return "dirty"  # fail closed: git cannot answer
        if not out.strip():
            return None
        return "untracked" if out.startswith("??") else "dirty"

    def write(self, doc: Doc) -> None:
        text = doc.text()
        handle, temp = tempfile.mkstemp(dir=doc.path.parent, prefix=".backfill-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                out.write(text)
            shutil.copymode(doc.path, temp)
            os.replace(temp, doc.path)
        except OSError:
            if os.path.exists(temp):
                os.unlink(temp)
            raise

    def run(self) -> int:
        self.load()
        for rule in self.rules:
            method = getattr(self, f"rule_{rule}")
            for doc in self.docs:
                if method(doc):
                    doc.hit.add(rule)
        counts = {rule: 0 for rule in self.rules}
        total = 0
        changed_files = 0
        skipped = 0
        for doc in self.docs:
            if not doc.hit:
                continue
            if not self.force:
                why = self.tree_state(doc.path)
                if why:
                    print(f"skipped ({why}) {doc.rel}")
                    skipped += 1
                    continue
            for rule in self.rules:
                if rule in doc.hit:
                    verbs = VERBS[rule]
                    print(f"{'would ' + verbs[0] if self.dry_run else verbs[1]} {doc.rel}")
                    counts[rule] += 1
                    total += 1
            changed_files += 1
            if not self.dry_run:
                self.write(doc)
        for rule in self.rules:
            print(f"rule ({rule}) {LABELS[rule]}: {counts[rule]} files")
        print(f"total: {total} changes in {changed_files} files")
        print(f"skipped: {skipped} files")
        for kind, rel, step, text in sorted(self.reports):
            print(f"{kind}: {rel} step {step}" + (f": {text}" if text else ""))
        if total == 0 and skipped == 0:
            print("nothing to do")
        return 0
