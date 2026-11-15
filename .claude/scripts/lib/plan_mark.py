#!/usr/bin/env python3
"""Stamp a plan step's start or finish with the real clock time, safely.

Usage: plan_mark.py <repo>/<plan> start|done <N>

Adds `**Started:** <ts>` / `**Done:** <ts>` (ISO-8601 local time with offset, seconds) to step N of the plan
`$PLANS_DIR/<repo>/{open,completed}/<topic>/<plan>.md` (open/ wins; PLANS_DIR default <stronghold>/plans).
Each stamp appears at most once per step: first write wins, a re-run changes nothing. Writes are serialized by an
exclusive flock on $PLAN_MARK_LOCK (default /tmp/claude/plan-mark.lock, 10 s bounded wait) and atomic.
Stdout: `marked <verb> step N` or `already marked <verb> step N`, plus `; rolled up to <repo>/<master> step M` when
`start` also wrote the master step (the sub-plan's `**Master plan:**` backref; retried on every `start`).
Exit: 0 ok | 2 usage | 3 malformed existing stamp | 4 plan or step not found | 5 ambiguous plan name |
6 lock timeout or lock/temp/chmod/replace OSError.
"""

from __future__ import annotations

import fcntl
import os
import re
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import plan_parse  # noqa: E402

LOCK_ENV = "PLAN_MARK_LOCK"
DEFAULT_LOCK = "/tmp/claude/plan-mark.lock"
LOCK_TIMEOUT_S = 10.0
LOCK_POLL_S = 0.1
VERBS = ("start", "done")
LABELS = {"start": "Started", "done": "Done"}
SPEC_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
FIELD_RES = (plan_parse.SUBPLAN_RE, plan_parse.BRANCH_RE, plan_parse.STATUS_RE)
STRICT = {"start": plan_parse.STARTED_RE, "done": plan_parse.DONE_RE}
H2_RE = re.compile(r"^## ")
NON_BRANCHES = ("", "main", "none")


class PlanNotFound(LookupError):
    """No plan file, or no step N, matched."""


class AmbiguousPlan(LookupError):
    def __init__(self, candidates: list[Path]) -> None:
        super().__init__("ambiguous plan name: " + ", ".join(str(c) for c in candidates))
        self.candidates = candidates


class MalformedStamp(ValueError):
    """An existing stamp line has an empty, spaced or non-ISO value."""


class LockError(Exception):
    """Base of LockTimeout and WriteError (exit 6)."""


class LockTimeout(LockError):
    pass


class WriteError(LockError):
    pass


def default_plans_dir() -> Path:
    env = os.environ.get("PLANS_DIR")
    return Path(env) if env else Path(__file__).resolve().parents[3] / "plans"


def resolve_plan(plans_dir: Path, spec: str) -> Path:
    """Resolve `<repo>/<plan>` like plan-status.sh: open/ wins over completed/; >1 match in the winner is ambiguous."""
    if not SPEC_RE.fullmatch(spec):
        raise ValueError(f"expected <repo>/<plan>, got {spec!r}")
    repo, name = spec.split("/")
    for state in plan_parse.STATES:
        matches = sorted((plans_dir / repo / state).glob(f"*/{name}.md"))
        if len(matches) > 1:
            raise AmbiguousPlan(matches)
        if matches:
            return matches[0]
    raise PlanNotFound(f"plan not found: {spec} (looked under {plans_dir}/{repo}/{{open,completed}}/<topic>/)")


def locked_rewrite(path: Path, fn: Callable[[str], str | None]) -> bool:
    """Re-read `path` under the exclusive plan-mark lock, apply fn, atomically write when fn returns text."""
    lock_path = Path(os.environ.get(LOCK_ENV) or DEFAULT_LOCK)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o666)
    except OSError as err:
        raise WriteError(f"cannot open lock {lock_path}: {err}") from err
    try:
        deadline = time.monotonic() + LOCK_TIMEOUT_S
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as err:
                if time.monotonic() >= deadline:
                    raise LockTimeout(f"timed out after {LOCK_TIMEOUT_S:g}s waiting for lock {lock_path}") from err
                time.sleep(LOCK_POLL_S)
        new_text = fn(path.read_text())  # FileNotFoundError propagates unchanged
        if new_text is None:
            return False
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".plan-mark-")
        except OSError as err:
            raise WriteError(f"cannot create temp file in {path.parent}: {err}") from err
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(new_text)
            os.chmod(tmp, path.stat().st_mode & 0o777)
            os.replace(tmp, path)
        except OSError as err:
            raise WriteError(f"cannot write {path}: {err}") from err
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return True
    finally:
        os.close(lock_fd)  # closing releases the flock


def _find_step(lines: list[str], n: int) -> tuple[int, int]:
    """Return (heading index, end index exclusive) of step N, mirroring parse_plan's fence/Progress exclusions."""
    _front, body_start = plan_parse._split_frontmatter(lines)
    fence: str | None = None
    section = ""
    start: int | None = None
    for index in range(body_start, len(lines)):
        line = lines[index]
        fenced = plan_parse.FENCE_RE.match(line)
        if fence is not None:
            if fenced and fenced.group(1)[0] == fence[0] and len(fenced.group(1)) >= len(fence):
                fence = None
            continue
        if fenced:
            fence = fenced.group(1)
            continue
        boundary = False
        if H2_RE.match(line):
            section = line[2:].strip().lower()
            boundary = True
        elif section != "progress tracking" and plan_parse.STEP_PREFIX_RE.match(line):
            found = plan_parse.STEP_RE.match(line)
            if found:
                boundary = True
                if start is None and int(found.group(1)) == n:
                    start = index
                    continue
        if boundary and start is not None:
            return start, index
    if start is None:
        raise PlanNotFound(f"no step {n} in plan")
    return start, len(lines)


def stamp_step(text: str, n: int, verb: str, ts: str) -> str | None:
    """Return text with step N's stamp added, or None when already stamped.

    Raises PlanNotFound (no step N) or MalformedStamp (existing stamp with a bad value).
    """
    label = LABELS[verb]
    lines = text.split("\n")
    head, end = _find_step(lines, n)
    prefix = f"**{label}:**"
    fence: str | None = None
    anchor = head
    started_at: int | None = None
    summarizing = False
    for index in range(head + 1, end):
        line = lines[index]
        fenced = plan_parse.FENCE_RE.match(line)
        if fence is not None:
            if fenced and fenced.group(1)[0] == fence[0] and len(fenced.group(1)) >= len(fence):
                fence = None
            continue
        if fenced:
            fence = fenced.group(1)
            summarizing = False
            continue
        if line.startswith(prefix):
            if not STRICT[verb].match(line) or plan_parse.parse_stamp(STRICT[verb].match(line).group(1)) is None:
                raise MalformedStamp(f"malformed **{label}:** stamp in step {n}: {line!r}")
            return None
        if plan_parse.STARTED_RE.match(line) or line.startswith("**Started:**"):
            started_at = index
        if any(regex.match(line) for regex in FIELD_RES):
            anchor = index
            summarizing = False
        elif plan_parse.SUMMARY_RE.match(line.strip()):
            summarizing = True
        elif summarizing and not plan_parse.SUMMARY_BULLET_RE.match(line) and line.strip():
            summarizing = False
    if verb == "done" and started_at is not None:
        anchor = started_at
    elif anchor > head and _inside_summary(lines, head, anchor):
        anchor = _after_summary(lines, anchor, end)
    lines.insert(anchor + 1, f"{prefix} {ts}")
    return "\n".join(lines)


def _inside_summary(lines: list[str], head: int, anchor: int) -> bool:
    """True when the anchor field line sits between a `**Summary:**` head and its bullets."""
    for index in range(anchor - 1, head, -1):
        line = lines[index]
        if plan_parse.SUMMARY_RE.match(line.strip()):
            return True
        if line.strip() and not plan_parse.SUMMARY_BULLET_RE.match(line):
            return False
    return False


def _after_summary(lines: list[str], anchor: int, end: int) -> int:
    index = anchor
    while index + 1 < end and plan_parse.SUMMARY_BULLET_RE.match(lines[index + 1]):
        index += 1
    return index


def now_stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def match_master_step(
    master_doc: plan_parse.PlanDoc, master_path: str | Path, sub_path: str | Path, sub_branch: str | None
) -> plan_parse.Step | None:
    """The master step that owns the sub-plan at `sub_path`, or None.

    Sub-plan link first (it must resolve to the sub-plan's real path); only for a step whose link is absent or the
    literal `none`, fall back to `**Branch:**` equal to `sub_branch`. A dangling link never falls back.
    Keep in step with `plan_lifecycle.Reconciler.find_sub` (reimplemented here because plan_lifecycle imports
    plan_mark). find_sub scans only the master's own topic folder (`Store.plan_files(master.parent)`), so the two
    matchers can differ for cross-topic masters.
    """
    real_sub = Path(sub_path).resolve()
    for step in master_doc.steps:
        link = step.sub_plan_link
        if link and link != "none":
            found = plan_parse.resolve_link(master_path, link)
            if found.kind != "dangling" and found.path is not None and found.path.resolve() == real_sub:
                return step
            continue
        if step.branch is not None and step.branch not in NON_BRANCHES and step.branch == sub_branch:
            return step
    return None


def _set_status(text: str, n: int, value: str) -> str:
    """Set (or insert after Branch/Sub-plan, else after the heading) step N's `**Status:**` line."""
    lines = text.split("\n")
    head, end = _find_step(lines, n)
    found: dict[str, int] = {}
    fence: str | None = None
    for index in range(head + 1, end):
        line = lines[index]
        fenced = plan_parse.FENCE_RE.match(line)
        if fence is not None:
            if fenced and fenced.group(1)[0] == fence[0] and len(fenced.group(1)) >= len(fence):
                fence = None
            continue
        if fenced:
            fence = fenced.group(1)
            continue
        for key, regex in (("status", plan_parse.STATUS_RE), ("branch", plan_parse.BRANCH_RE), ("sub", plan_parse.SUBPLAN_RE)):
            if regex.match(line):
                found.setdefault(key, index)
    line = f"**Status:** {value}"
    if "status" in found:
        lines[found["status"]] = line
    else:
        lines.insert(max(found.get("branch", head), found.get("sub", head)) + 1, line)
    return "\n".join(lines)


def roll_up(sub_path: Path) -> str | None:
    """Copy the sub-plan's earliest `**Started:**` to its master step (and flip todo -> in-progress).

    Returns `<master-repo>/<master> step M` when the master write happened, else None (warnings and notes go to
    stderr, never an error). Raises LockError when the master write fails (the sub-plan stamp already stands).
    """
    sub_doc = plan_parse.parse_plan(sub_path)
    if sub_doc.master_plan_link is None:
        return None
    found = plan_parse.resolve_link(sub_path, sub_doc.master_plan_link)
    if found.kind == "dangling" or found.path is None or not found.path.is_file():
        print(f"plan-mark: warning: master plan link {sub_doc.master_plan_link!r} does not resolve; no roll-up", file=sys.stderr)
        return None
    master = found.path
    reason = plan_parse.reconcile_skip_reason(plan_parse.parse_plan(master), master)
    if reason is not None:
        print(f"plan-mark: note: master {master.name} is ignored by reconcile ({reason}); no roll-up", file=sys.stderr)
        return None
    starts = [(parse, step.started) for step in sub_doc.steps if (parse := plan_parse.parse_stamp(step.started)) is not None]
    if not starts:
        return None
    earliest = min(starts, key=lambda pair: pair[0].timestamp())[1]
    sub_branch = sub_doc.frontmatter.get("branch")
    outcome: dict[str, object] = {}

    def fn(text: str) -> str | None:
        doc = plan_parse.parse_text(text, master)
        step = match_master_step(doc, master, sub_path, sub_branch)
        if step is None:
            outcome["nomatch"] = True
            return None
        if plan_parse.parse_stamp(step.started) is not None:
            return None
        new = text
        if step.status is None or step.status.startswith("todo"):
            new = _set_status(new, step.n, "in-progress")
        try:
            stamped = stamp_step(new, step.n, "start", earliest)
        except MalformedStamp as err:
            print(f"plan-mark: note: master roll-up skipped the stamp: {err}", file=sys.stderr)
            stamped = None
        if stamped is not None:
            new = stamped
        outcome["step"] = step.n
        return new if new != text else None

    try:
        wrote = locked_rewrite(master, fn)
    except FileNotFoundError:
        print(f"plan-mark: warning: master plan {master} vanished; no roll-up", file=sys.stderr)
        return None
    if outcome.get("nomatch"):
        print(f"plan-mark: warning: no step of {master.name} matches {sub_path.name}; no roll-up", file=sys.stderr)
        return None
    if not wrote:
        return None
    return f"{master.parents[2].name}/{master.stem} step {outcome['step']}"


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[2] not in VERBS or not re.fullmatch(r"[0-9]+", argv[3]):
        print(__doc__, file=sys.stderr)
        return 2
    spec, verb, n = argv[1], argv[2], int(argv[3])
    try:
        path = resolve_plan(default_plans_dir(), spec)
    except ValueError as err:
        print(f"plan-mark: {err}", file=sys.stderr)
        return 2
    except AmbiguousPlan as err:
        print(f"plan-mark: ambiguous plan name {spec}; candidates:", file=sys.stderr)
        for candidate in err.candidates:
            print(f"  {candidate}", file=sys.stderr)
        return 5
    except PlanNotFound as err:
        print(f"plan-mark: {err}", file=sys.stderr)
        return 4
    ts = now_stamp()
    try:
        changed = locked_rewrite(path, lambda text: stamp_step(text, n, verb, ts))
    except MalformedStamp as err:
        print(f"plan-mark: {err}", file=sys.stderr)
        return 3
    except PlanNotFound as err:
        print(f"plan-mark: {err} {path}", file=sys.stderr)
        return 4
    except FileNotFoundError as err:
        print(f"plan-mark: plan vanished: {err}", file=sys.stderr)
        return 4
    except LockError as err:
        print(f"plan-mark: {err}", file=sys.stderr)
        return 6
    line = f"{'marked' if changed else 'already marked'} {verb} step {n}"
    if verb == "start":
        try:
            rolled = roll_up(path)
        except LockError as err:
            print(line)
            print(f"plan-mark: master roll-up failed, retry `start {n}` to heal: {err}", file=sys.stderr)
            return 6
        if rolled:
            line += f"; rolled up to {rolled}"
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
