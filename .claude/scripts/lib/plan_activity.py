#!/usr/bin/env python3
"""Time-ordered plan activity feed: git history of the plans folder merged with step `**Started:**`/`**Done:**` stamps.

Usage: plan_activity.py [--repo R] [--limit N] [--json] [--plans-dir D]

One line per event, newest first: `YYYY-MM-DD HH:MM  <kind>  <repo>/<topic>[/<name>]  [step N: title]  (<state>)`
(local time). Env: PLANS_DIR (default <stronghold>/plans). With git history unavailable (not a repo, git missing,
failure, timeout) the stamp events are still printed and the reason goes to stderr.
Exit: 0 ok | 2 usage | 4 unknown repo bucket.

Library: `build_feed(plans_dir)` -> {"events": [...], "history": "ok" | "unavailable", "generated_at": iso}. Git output
is cached per (resolved root, plans dir name) at the HEAD it was read at; a failed read is negative-cached.
Events never carry commit subjects, full paths or frontmatter other than `short_title`. The last event field,
`step_state`, is the CURRENT state of the event's step ("done" iff it has a parseable `**Done:**`, else "open") for
`step-*` / `subplan-*` events, and None for git events and `implementation-started`.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import plan_mark  # noqa: E402
import plan_naming  # noqa: E402
import plan_parse  # noqa: E402

SENTINEL = "#PA#"
GIT_TIMEOUT_S = 20.0
NEG_TTL_S = 45.0
KIND_ORDER = (
    "completed",
    "subplan-done",
    "step-done",
    "progress",
    "edited",
    "review",
    "artifact",
    "subplan-started",
    "step-started",
    "implementation-started",
    "created",
    "reopened",
)
ARTIFACT_DIRS = ("research", "mocks", "split")
DOC_SUFFIXES = (".md", ".html")
TRACKING_PREFIXES = ("**Started:**", "**Done:**")

_LOCK = threading.Lock()
_POSITIVE: dict[tuple[str, str], tuple[str, str, str]] = {}
_NEGATIVE: dict[tuple[str, str, str | None], float] = {}


@dataclass(frozen=True)
class Event:
    ts: str
    epoch: int
    kind: str
    repo: str
    topic: str
    name: str | None
    title: str
    plan_kind: str | None
    state: str
    step: int | None
    step_title: str | None
    detail: str | None
    sha: str | None
    source: str
    step_state: str | None = None


@dataclass(frozen=True)
class PlanInfo:
    repo: str
    state: str
    topic: str
    name: str
    path: Path
    doc: plan_parse.PlanDoc
    title: str
    short_title: str | None
    plan_kind: str | None


def reset_cache() -> None:
    """Test seam: clear the positive cache, the negative cache and the single-flight state."""
    global _LOCK
    _POSITIVE.clear()
    _NEGATIVE.clear()
    _LOCK = threading.Lock()


# --- git -------------------------------------------------------------------------------------------------------------


def _log(message: str) -> None:
    print(f"plan_activity: {message}", file=sys.stderr)


def _git(root: Path, args: list[str], budget: float, label: str) -> str | None:
    """Run one git command under `budget` seconds; None (reason + duration on stderr) on any failure."""
    argv = ["git", "-C", str(root), "--no-pager", "-c", "core.quotepath=false", *args]
    env = {**os.environ, "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1"}
    started = time.monotonic()
    if budget <= 0:
        _log(f"git {label}: no time left in the {GIT_TIMEOUT_S:.0f}s budget")
        return None
    try:
        result = subprocess.run(
            argv, capture_output=True, encoding="utf-8", errors="replace", timeout=budget, env=env, check=False
        )
    except FileNotFoundError:
        _log(f"git {label}: git not found on PATH")
        return None
    except subprocess.TimeoutExpired:
        _log(f"git {label}: timed out after {time.monotonic() - started:.2f}s (limit {budget:.1f}s)")
        return None
    except OSError as err:
        _log(f"git {label}: could not run git: {err}")
        return None
    took = time.monotonic() - started
    if result.returncode != 0:
        reason = (result.stderr or "").strip().splitlines()
        _log(f"git {label}: rc={result.returncode} after {took:.2f}s: {reason[0] if reason else 'no stderr'}")
        return None
    return result.stdout


def _rev_parse(root: Path, deadline: float) -> str | None:
    out = _git(root, ["rev-parse", "HEAD"], deadline - time.monotonic(), "rev-parse")
    head = (out or "").strip()
    return head or None


def _logs(root: Path, plans_name: str, deadline: float) -> tuple[str, str] | None:
    name_status = _git(
        root,
        [
            "log",
            "-M",
            "--relative",
            "--no-color",
            f"--format={SENTINEL}%H%x09%h%x09%at%x09%aI",
            "--name-status",
            "--",
            plans_name,
        ],
        deadline - time.monotonic(),
        "log name-status",
    )
    if name_status is None:
        return None
    patch = _git(
        root,
        [
            "log",
            "-M",
            "--relative",
            "--diff-filter=M",
            "-p",
            "-U0",
            "--no-color",
            f"--format={SENTINEL}%H",
            "--",
            f":(glob){plans_name}/*/*/*/*.md",
        ],
        deadline - time.monotonic(),
        "log patch",
    )
    if patch is None:
        return None
    return name_status, patch


def read_git_history(root: Path, plans_name: str = "plans", timeout: float = GIT_TIMEOUT_S) -> tuple[str, str] | None:
    """(name-status text, patch text) of git history for `<root>/<plans_name>`, or None (reason on stderr)."""
    if not plan_naming.valid_segment(plans_name):
        _log(f"plans dir name {plans_name!r} is not a valid path segment; history unavailable")
        return None
    deadline = time.monotonic() + timeout
    if _rev_parse(Path(root), deadline) is None:
        return None
    return _logs(Path(root), plans_name, deadline)


def _cached_history(root: Path, plans_name: str) -> tuple[str, str] | None:
    """Single-flight, HEAD-keyed git read with a bounded positive cache and a negative cache for failures."""
    if not plan_naming.valid_segment(plans_name):
        _log(f"plans dir name {plans_name!r} is not a valid path segment; history unavailable")
        return None
    root = Path(root).resolve()
    key = (str(root), plans_name)
    with _LOCK:
        now = time.monotonic()
        if _NEGATIVE.get((*key, None), 0.0) > now:
            return None
        deadline = now + GIT_TIMEOUT_S
        head = _rev_parse(root, deadline)
        if head is None:
            _NEGATIVE[(*key, None)] = time.monotonic() + NEG_TTL_S
            return None
        hit = _POSITIVE.get(key)
        if hit is not None and hit[0] == head:
            return hit[1], hit[2]
        if _NEGATIVE.get((*key, head), 0.0) > time.monotonic():
            return None
        history = _logs(root, plans_name, deadline)
        if history is None:
            _NEGATIVE[(*key, head)] = time.monotonic() + NEG_TTL_S
            return None
        _POSITIVE[key] = (head, history[0], history[1])
        return history


# --- parsing helpers -------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Commit:
    sha: str
    epoch: int
    ts: str
    entries: tuple[tuple[str, tuple[str, ...]], ...]  # (status letter, paths)


def _parse_name_status(text: str) -> list[_Commit]:
    commits: list[_Commit] = []
    head: tuple[str, int, str] | None = None
    entries: list[tuple[str, tuple[str, ...]]] = []

    def flush() -> None:
        if head is not None:
            commits.append(_Commit(head[0], head[1], head[2], tuple(entries)))

    for line in text.splitlines():
        if line.startswith(SENTINEL):
            flush()
            entries = []
            fields = line[len(SENTINEL) :].split("\t")
            try:
                head = (fields[0][:7], int(fields[2]), fields[3])
            except (IndexError, ValueError):
                head = None
            continue
        if head is None or "\t" not in line:
            continue
        status, *paths = line.split("\t")
        if status and status[0] in "AMDRCT":
            entries.append((status[0], tuple(paths)))
    flush()
    return commits


def _split(path: str) -> list[str] | None:
    """Segments of a plans-relative path (first segment is the plans dir), or None when it is not addressable."""
    parts = path.split("/")
    if len(parts) < 5 or "tmp" in parts or not all(plan_naming.valid_segment(p) for p in parts):
        return None
    if parts[2] not in plan_parse.STATES:
        return None
    return parts


@dataclass(frozen=True)
class _Move:
    repo: str
    topic: str
    rest: tuple[str, ...]  # path segments under the topic dir
    old: str
    new: str


def _moves(entries: tuple[tuple[str, tuple[str, ...]], ...]) -> list[_Move]:
    """State moves in one commit: `R` across states, plus same-commit `D`+`A` pairs (rename detection missed them)."""
    found: list[_Move] = []
    deleted: dict[tuple[str, str, str, tuple[str, ...]], bool] = {}
    added: list[tuple[str, str, str, tuple[str, ...]]] = []
    for status, paths in entries:
        if status == "R" and len(paths) == 2:
            old, new = _split(paths[0]), _split(paths[1])
            if old and new and old[1] == new[1] and old[3] == new[3] and old[2] != new[2]:
                found.append(_Move(old[1], old[3], tuple(old[4:]), old[2], new[2]))
        elif status == "D" and len(paths) == 1:
            parts = _split(paths[0])
            if parts:
                deleted[(parts[1], parts[3], parts[2], tuple(parts[4:]))] = True
        elif status == "A" and len(paths) == 1:
            parts = _split(paths[0])
            if parts:
                added.append((parts[1], parts[3], parts[2], tuple(parts[4:])))
    for repo, topic, state, rest in added:
        other = "open" if state == "completed" else "completed"
        if deleted.get((repo, topic, other, rest)):
            found.append(_Move(repo, topic, rest, other, state))
    return found


def state_transitions(name_status_text: str) -> dict[tuple[str, str], list[tuple[int, str]]]:
    """Per (repo, topic): ascending (epoch, destination state) for every open<->completed move in history."""
    out: dict[tuple[str, str], list[tuple[int, str]]] = {}
    for commit in reversed(_parse_name_status(name_status_text)):
        seen: set[tuple[str, str, str]] = set()
        for move in _moves(commit.entries):
            if (move.repo, move.topic, move.new) not in seen:
                seen.add((move.repo, move.topic, move.new))
                out.setdefault((move.repo, move.topic), []).append((commit.epoch, move.new))
    for items in out.values():
        items.sort(key=lambda item: item[0])
    return out


def _parse_patch(text: str) -> dict[tuple[str, str], tuple[list[str], list[str]]]:
    """(sha7, plans-relative path) -> (removed lines, added lines), without the +/- prefix."""
    out: dict[tuple[str, str], tuple[list[str], list[str]]] = {}
    sha = ""
    key: tuple[str, str] | None = None
    in_hunk = False
    for line in text.splitlines():
        if line.startswith(SENTINEL):
            sha = line[len(SENTINEL) :].strip()[:7]
            key = None
            in_hunk = False
        elif line.startswith("diff --git "):
            key = (sha, line.split(" ")[-1][2:])
            out.setdefault(key, ([], []))
            in_hunk = False
        elif line.startswith("@@"):
            in_hunk = True
        elif in_hunk and key is not None and line[:1] == "+":
            out[key][1].append(line[1:])
        elif in_hunk and key is not None and line[:1] == "-":
            out[key][0].append(line[1:])
    return out


def _is_tracking(line: str) -> bool:
    text = line.strip()
    return bool(
        plan_parse.STATUS_RE.match(text) or plan_parse.FINISHED_RE.match(text) or text.startswith(TRACKING_PREFIXES)
    )


def _classify_modify(removed: list[str], added: list[str]) -> tuple[str, int]:
    """('edited', 0) | ('progress', ticks) | ('drop', 0) for one modified plan file in one commit."""
    removed = [line for line in removed if line.strip()]
    added = [line for line in added if line.strip()]
    if not removed and not added:
        return "edited", 0
    pool = Counter()
    for line in removed:
        box = plan_parse.BOX_RE.match(line)
        if box:
            pool[(box.group(2), box.group(1))] += 1
    ticks = 0
    for line in added:
        box = plan_parse.BOX_RE.match(line)
        if box:
            text, marker = box.group(2), box.group(1)
            previous = next((m for m in (" ", "x", "X", "~") if m != marker and pool[(text, m)] > 0), None)
            if previous is None:
                return "edited", 0
            pool[(text, previous)] -= 1
            if previous == " " and marker in "xX":
                ticks += 1
        elif not _is_tracking(line):
            return "edited", 0
    for line in removed:
        box = plan_parse.BOX_RE.match(line)
        if not box and not _is_tracking(line):
            return "edited", 0
    if any(count > 0 for count in pool.values()):
        return "edited", 0
    if any(line.strip().startswith(TRACKING_PREFIXES) for line in added):
        return "drop", 0
    return "progress", ticks


def _review_target(review: str, stems: set[str]) -> str | None:
    if review.startswith("push-review-"):
        return None
    matches = [stem for stem in stems if review.startswith(stem)]
    return max(matches, key=len) if matches else None


def _info_fields(index: dict, repo: str, topic: str, name: str | None) -> tuple[str, str | None, bool]:
    """(title, plan_kind, skip) for an event, from the current tree; skip is True for a filtered non-plan."""
    if name is None:
        return topic, None, False
    info = index.get((repo, topic), {}).get(name)
    if info is None:
        return name, None, False
    if info.plan_kind is None:
        return name, None, True
    return info.short_title or info.title or name, info.plan_kind, False


def git_events(name_status_text: str, patch_text: str, plans_index: dict) -> list[Event]:
    """Events from git history. `plans_index` is `{(repo, topic): {name: PlanInfo}}` over the current tree."""
    patches = _parse_patch(patch_text)
    events: list[Event] = []
    for commit in _parse_name_status(name_status_text):
        moves = _moves(commit.entries)
        moved_new = {(m.repo, m.topic, m.new, m.rest) for m in moves}
        stems: dict[tuple[str, str], set[str]] = {}
        for status, paths in commit.entries:
            parts = _split(paths[-1]) if paths else None
            if parts and len(parts) == 5 and parts[4].endswith(".md"):
                stems.setdefault((parts[1], parts[3]), set()).add(parts[4][:-3])

        def emit(kind, repo, topic, name, state, detail=None, _commit=commit) -> None:
            title, plan_kind, skip = _info_fields(plans_index, repo, topic, name)
            if not skip:
                events.append(
                    Event(_commit.ts, _commit.epoch, kind, repo, topic, name, title, plan_kind, state, None, None,
                          detail, _commit.sha, "git")
                )

        plan_moves = [m for m in moves if len(m.rest) == 1 and m.rest[0].endswith(".md")]
        for move in plan_moves:
            emit("completed" if move.new == "completed" else "reopened", move.repo, move.topic, move.rest[0][:-3], move.new)
        topic_moves = {(m.repo, m.topic, m.new) for m in moves} - {(m.repo, m.topic, m.new) for m in plan_moves}
        for repo, topic, new in sorted(topic_moves):
            emit("completed" if new == "completed" else "reopened", repo, topic, None, new)

        artifacts: set[tuple[str, str, str, str]] = set()
        for status, paths in commit.entries:
            if status not in ("A", "M", "R") or not paths:
                continue
            path = paths[-1]
            parts = _split(path)
            if parts is None:
                continue
            repo, state, topic, rest = parts[1], parts[2], parts[3], tuple(parts[4:])
            if (repo, topic, state, rest) in moved_new:
                continue
            if status == "R":
                old = _split(paths[0])
                if old is None or old[2] != state:
                    continue
            suffix = Path(rest[-1]).suffix
            if suffix not in DOC_SUFFIXES:
                continue
            if len(rest) == 1 and suffix == ".md":
                name = rest[0][:-3]
                if status == "A":
                    emit("created", repo, topic, name, state)
                elif status == "R":
                    emit("edited", repo, topic, name, state)
                else:
                    kind, ticks = _classify_modify(*patches.get(((commit.sha), path), ([], [])))
                    if kind == "progress":
                        emit("progress", repo, topic, name, state, f"{ticks} boxes ticked")
                    elif kind == "edited":
                        emit("edited", repo, topic, name, state)
            elif rest[0] == "reviews" and suffix == ".md" and status in ("A", "M"):
                review = Path(rest[-1]).stem
                known = set(stems.get((repo, topic), set())) | set(plans_index.get((repo, topic), {}))
                target = _review_target(review, known)
                detail = "push review" if review.startswith("push-review-") else "plan review"
                emit("review", repo, topic, target, state, detail)
            elif rest[0] in ARTIFACT_DIRS and status == "A":
                marker = (repo, topic, state, rest[0])
                if marker not in artifacts:
                    artifacts.add(marker)
                    emit("artifact", repo, topic, None, state, rest[0])
    return events


# --- stamps ----------------------------------------------------------------------------------------------------------


def scan_plans(plans_dir: Path) -> list[PlanInfo]:
    """Every plan file under <plans_dir>/<repo>/{open,completed}/<topic>/*.md in the current tree."""
    infos: list[PlanInfo] = []
    try:
        repos = sorted(p for p in Path(plans_dir).iterdir() if p.is_dir())
    except OSError:
        return infos
    for repo_dir in repos:
        if not plan_naming.valid_segment(repo_dir.name):
            continue
        for state in plan_parse.STATES:
            try:
                topics = sorted(p for p in (repo_dir / state).iterdir() if p.is_dir())
            except OSError:
                continue
            for topic_dir in topics:
                if not plan_naming.valid_segment(topic_dir.name):
                    continue
                try:
                    files = sorted(p for p in topic_dir.iterdir() if p.suffix == ".md" and p.is_file())
                except OSError:
                    continue
                for path in files:
                    if not plan_naming.valid_segment(path.stem):
                        continue
                    try:
                        lines = path.read_text().splitlines()
                    except (OSError, UnicodeDecodeError):
                        continue
                    doc = plan_parse.parse_plan(path)
                    kind = plan_naming.kind(path, doc, lines)
                    infos.append(
                        PlanInfo(
                            repo_dir.name,
                            state,
                            topic_dir.name,
                            path.stem,
                            path,
                            doc,
                            plan_naming.title(lines, path.stem),
                            plan_naming.short_title(doc),
                            kind,
                        )
                    )
    return infos


def plans_index(infos: list[PlanInfo]) -> dict:
    """{(repo, topic): {name: PlanInfo}}; an open copy wins over a completed one."""
    index: dict[tuple[str, str], dict[str, PlanInfo]] = {}
    for info in sorted(infos, key=lambda i: i.state != "open"):
        index.setdefault((info.repo, info.topic), {}).setdefault(info.name, info)
    return index


def _state_at(transitions: dict, repo: str, topic: str, epoch: int, current: str) -> str:
    items = transitions.get((repo, topic))
    if not items:
        return current
    state = "open" if items[0][1] == "completed" else "completed"
    for when, dest in items:
        if when <= epoch:
            state = dest
    return state


def _stamp_epoch(value: str | None) -> tuple[str, int] | None:
    parsed = plan_parse.parse_stamp(value)
    if parsed is None:
        return None
    return (value or "").strip(), int(parsed.timestamp())


def _subplan_stem(info: PlanInfo, step: plan_parse.Step, siblings: list[PlanInfo]) -> str | None:
    """Stem of the sub-plan the roll-up would match to this master step (via `plan_mark.match_master_step`)."""
    link = step.sub_plan_link
    candidates: list[Path] = []
    if link and link != "none":
        found = plan_parse.resolve_link(info.path, link)
        if found.kind != "dangling" and found.path is not None and found.path.suffix == ".md":
            candidates = [found.path]
    else:
        candidates = [s.path for s in siblings if s.path != info.path]
    for candidate in candidates:
        try:
            branch = plan_parse.parse_plan(candidate).frontmatter.get("branch")
        except OSError:
            continue
        if plan_mark.match_master_step(info.doc, info.path, candidate, branch) is step:
            return candidate.stem
    return None


def stamp_events(plans_dir: Path, transitions: dict, infos: list[PlanInfo] | None = None) -> list[Event]:
    """Events from `**Started:**` / `**Done:**` stamps in the current tree (both states)."""
    infos = scan_plans(plans_dir) if infos is None else infos
    events: list[Event] = []
    by_topic: dict[tuple[str, str, str], list[PlanInfo]] = {}
    for info in infos:
        by_topic.setdefault((info.repo, info.state, info.topic), []).append(info)
    for info in infos:
        if info.plan_kind is None:
            continue
        title = info.short_title or info.title or info.name
        is_rollup = info.plan_kind == "master" and plan_parse.reconcile_skip_reason(info.doc, info.path) is None
        siblings = by_topic[(info.repo, info.state, info.topic)]
        earliest: Event | None = None
        earliest_step: plan_parse.Step | None = None

        def make(kind, ts, epoch, step, detail, _info=info, _title=title) -> Event:
            state = _state_at(transitions, _info.repo, _info.topic, epoch, _info.state)
            step_state = None
            if kind != "implementation-started":
                step_state = "done" if plan_parse.parse_stamp(step.done_at) is not None else "open"
            return Event(ts, epoch, kind, _info.repo, _info.topic, _info.name, _title, _info.plan_kind, state,
                         step.n, step.title, detail, None, "stamp", step_state)

        for step in info.doc.steps:
            sub = _subplan_stem(info, step, siblings) if is_rollup else None
            for value, verb in ((step.started, "started"), (step.done_at, "done")):
                stamp = _stamp_epoch(value)
                if stamp is None:
                    continue
                kind = f"subplan-{verb}" if sub else f"step-{verb}"
                event = make(kind, stamp[0], stamp[1], step, sub)
                events.append(event)
                if verb == "started" and (earliest is None or event.epoch < earliest.epoch):
                    earliest, earliest_step = event, step
        if earliest is not None and earliest_step is not None:
            events.append(make("implementation-started", earliest.ts, earliest.epoch, earliest_step, None))
    return events


def merge(events: list[Event]) -> list[Event]:
    """Newest first by epoch; ties by kind order, then repo/topic/name."""
    rank = {kind: i for i, kind in enumerate(KIND_ORDER)}
    return sorted(
        events,
        key=lambda e: (-e.epoch, rank.get(e.kind, len(rank)), e.repo, e.topic, e.name or ""),
    )


def build_feed(plans_dir: Path) -> dict:
    plans_dir = Path(plans_dir)
    infos = scan_plans(plans_dir)
    index = plans_index(infos)
    history = _cached_history(plans_dir.parent, plans_dir.name)
    if history is None:
        events = stamp_events(plans_dir, {}, infos)
    else:
        transitions = state_transitions(history[0])
        events = git_events(history[0], history[1], index) + stamp_events(plans_dir, transitions, infos)
    return {
        "events": [asdict(event) for event in merge(events)],
        "history": "ok" if history is not None else "unavailable",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


# --- CLI -------------------------------------------------------------------------------------------------------------


def format_event(event: dict) -> str:
    when = datetime.fromtimestamp(event["epoch"]).strftime("%Y-%m-%d %H:%M")
    where = "/".join(p for p in (event["repo"], event["topic"], event["name"]) if p)
    step = f"  step {event['step']}: {event['step_title']}" if event["step"] is not None else ""
    return f"{when}  {event['kind']}  {where}{step}  ({event['state']})"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="plan-activity.sh", description="Time-ordered plan activity feed.")
    parser.add_argument("--repo", help="only this repo bucket")
    parser.add_argument("--limit", type=int, help="newest N events")
    parser.add_argument("--json", action="store_true", help="print the feed as JSON")
    parser.add_argument("--plans-dir", type=Path, help="plans dir (default: $PLANS_DIR or <stronghold>/plans)")
    try:
        args = parser.parse_args(argv[1:])
    except SystemExit as err:
        return 0 if err.code == 0 else 2
    if args.limit is not None and args.limit < 0:
        print("plan-activity: --limit must be >= 0", file=sys.stderr)
        return 2
    plans_dir = args.plans_dir or plan_mark.default_plans_dir()
    if args.repo and not (plan_naming.valid_segment(args.repo) and (plans_dir / args.repo).is_dir()):
        print(f"plan-activity: unknown repo bucket: {args.repo}", file=sys.stderr)
        return 4
    feed = build_feed(plans_dir)
    events = [e for e in feed["events"] if not args.repo or e["repo"] == args.repo]
    if args.limit is not None:
        events = events[: args.limit]
    if feed["history"] == "unavailable":
        print("plan-activity: git history unavailable; showing step timestamps only", file=sys.stderr)
    if args.json:
        print(json.dumps({**feed, "events": events}, indent=2))
    else:
        for event in events:
            print(format_event(event))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
