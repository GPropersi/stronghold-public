#!/usr/bin/env python3
"""Core of plan-lifecycle.sh: deterministic open -> completed lifecycle for the central plans store (stdlib only).

    plan_lifecycle.py [--stronghold <root>] <command> ...

Commands (<repo> is a sub-repo DIRECTORY name or `stronghold`, mapped to a bucket via the `Plans bucket` seam key):
  complete <repo> <topic> [--dry-run] [--close-issues]
                                        every topic-root plan must not say `finished: false` and at least one must say
                                        `finished: true`; `git mv` open/<topic> -> completed/<topic>. The topic's GitHub
                                        issues are closed here only for the stronghold (no PRs); a PR-based repo closes
                                        them through the PR's `Closes #N`, or pass --close-issues / run close-issues later.
  close-issues <repo> [<topic>] [--dry-run]
                                        close the OPEN GitHub issue of every completed plan (one topic or all). An issue
                                        also named by a plan still in open/, or by an OPEN PR's closing references, is
                                        left open. In a PR repo an issue is closed only once a MERGED PR is found for the
                                        plan's `branch:` or closing the issue (finished plans are archived before merging).
  audit [<repo>]                        report drift, exit 1 when any: a completed plan whose issue is still open
                                        (ISSUE-OPEN) and a review-only open/ topic: no plan file, a push review, no research/mocks (ORPHAN). Info only: a
                                        closed issue on a still-open plan (ISSUE-CLOSED), an open issue awaiting an open
                                        PR (ISSUE-PENDING).
  sweep [<repo>] [--dry-run]            `git mv` every ORPHAN open/ topic to completed/ (skipped when completed/<topic> exists).
  reconcile <repo> [<topic>] [--merged] [--dry-run]
                                        keep master plans in sync with their sub-plans. For each master step (sub-plan found
                                        by its **Sub-plan:** link, else by frontmatter `branch:` equal to the step's **Branch:**)
                                        whose sub-plan says `finished: true`: tick the step's unmarked boxes and set
                                        **Status:** done (PR repos: `done — awaiting merge (date)`); a no-PR bucket also
                                        converts `(post-merge)` boxes. --merged (PR repos) also scans completed/ topics, looks
                                        up the sub-plan branch's merged PR via gh and sets `merged — PR #N (date, sha)`,
                                        ticking `(post-merge)` boxes. A sub-plan with `split_into:` (its branch was never
                                        pushed) is merged only when EVERY part PR is, and gets `merged — PRs #A–#B (date, sha)`.
                                        Never unticks; `(manual, post-merge)` is never touched.
                                        Exit 1 when a named topic matches no master step.
  backfill [--dry-run] [--only <rule>] [--force]
                                        idempotent migration of every plan in the store to the plan schema (rules a-g, see
                                        plan_backfill.py); skips dirty/untracked plan files unless --force; prints a per-rule summary.
  review-topic <repo> <name>            ensure plans/<bucket>/completed/<name>/{reviews,tmp} exists for a plan-less push
                                        review (an existing open/ or completed/ topic of that name is reused); prints its path.

A plan whose `github_issue_url` names a different repo than the bucket's `Repo slug` is ignored everywhere: a repo
that moved restarts issue numbering, so its recorded number could hit an unrelated issue in the new repo.

Env: PLAN_LIFECYCLE_GH  gh wrapper to run (default: gh-bot.sh beside this script's parent); it runs with the repo
                        directory as cwd because the bot installation is picked from the cwd's origin.
Exit: 0 ok | 1 audit found drift | 2 usage | 3 topic not finishable | 4 unavailable (missing bucket/topic/slug, gh failed)
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB_DIR))

import plan_backfill  # noqa: E402
import plan_parse  # noqa: E402
import wf_frontmatter  # noqa: E402
import wf_lint_base as base  # noqa: E402

STATES = ("open", "completed")
NUMBER_RE = re.compile(r"^[0-9]+$")
SLUG_RE = re.compile(r"[:/]([^/:]+/[^/]+?)(?:\.git)?/?$")
ISSUE_URL_RE = re.compile(r"github\.com/([^/\s]+/[^/\s]+)/(?:issues|pull)/[0-9]+")
GH_TIMEOUT = 90
NON_PLAN_TYPES = plan_backfill.NON_PLAN_TYPES

OK, DRIFT, USAGE, UNFINISHED, UNAVAILABLE = 0, 1, 2, 3, 4


class Unavailable(Exception):
    pass


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.plans = root / "plans"
        self.ctx = base.Context(root, Path(os.environ.get("HOME", str(Path.home()))), base.load_config(root))
        self.gh = os.environ.get("PLAN_LIFECYCLE_GH") or str(LIB_DIR.parent / "gh-bot.sh")
        self._states: dict[str, dict[int, str]] = {}

    # --- repo / bucket resolution -------------------------------------------------------------

    def bucket_for(self, repo: str) -> str:
        if repo in self.ctx.cfg["fixed_buckets"]:
            return self.ctx.cfg["fixed_buckets"][repo]
        bucket = self.ctx.bucket_of(repo)
        if not bucket:
            raise Unavailable(f'missing seam key "Plans bucket" in {self.root / repo / "CLAUDE.md"}')
        return bucket

    def repo_dir_for_bucket(self, bucket: str) -> tuple[Path, str] | None:
        """(directory to run gh in, display name) for a bucket; None when no repo owns it."""
        if bucket in self.ctx.cfg["fixed_buckets"].values():
            return self.root, "stronghold"
        repo = self.ctx.repo_for_bucket(bucket)
        return (self.root / repo, repo) if repo else None

    def slug_for(self, bucket: str) -> str:
        found = self.repo_dir_for_bucket(bucket)
        if found is None:
            raise Unavailable(f"no repo owns the plans bucket `{bucket}`")
        directory, name = found
        if name == "stronghold":
            code, out = base.git(directory, "remote", "get-url", "origin")
            match = SLUG_RE.search(out.strip()) if code == 0 else None
            if not match:
                raise Unavailable("stronghold origin remote has no owner/repo slug")
            return match.group(1)
        slug = self.ctx.seam(name).token("Repo slug")
        if not slug:
            raise Unavailable(f'missing seam key "Repo slug" in {directory / "CLAUDE.md"}')
        return slug

    # --- plans --------------------------------------------------------------------------------

    def buckets(self, repo: str | None) -> list[str]:
        if repo:
            return [self.bucket_for(repo)]
        try:
            return sorted(p.name for p in self.plans.iterdir() if p.is_dir())
        except OSError:
            return []

    def topics(self, bucket: str, state: str) -> list[Path]:
        try:
            return sorted(p for p in (self.plans / bucket / state).iterdir() if p.is_dir())
        except OSError:
            return []

    @staticmethod
    def topic_markdown(topic: Path) -> list[Path]:
        """Every topic-root `*.md`, whatever its `type:` (unfiltered)."""
        return sorted(p for p in topic.glob("*.md") if p.is_file())

    @staticmethod
    def plan_files(topic: Path) -> list[Path]:
        """Topic-root markdown that counts as a plan: skips only the six explicit non-plan types
        (fail closed: untyped or unrecognised `type:` is a plan)."""
        return [p for p in Store.topic_markdown(topic) if wf_frontmatter.parse(p).get("type") not in NON_PLAN_TYPES]

    def issue_of(self, plan: Path, bucket: str) -> int | None:
        """The plan's issue number; None when absent or when its `github_issue_url` names a different repo
        than the bucket's (a repo that moved restarts numbering, so the number would hit an unrelated issue)."""
        meta = wf_frontmatter.parse(plan)
        value = meta.get("github_issue", "")
        if not NUMBER_RE.match(value):
            return None
        match = ISSUE_URL_RE.search(meta.get("github_issue_url", ""))
        if match and match.group(1).lower() != self.slug_for(bucket).lower():
            return None
        return int(value)

    def open_issue_numbers(self, bucket: str, ignore_topic: str | None = None) -> set[int]:
        found: set[int] = set()
        for topic in self.topics(bucket, "open"):
            if topic.name == ignore_topic:
                continue
            for plan in self.plan_files(topic):
                number = self.issue_of(plan, bucket)
                if number is not None:
                    found.add(number)
        return found

    # --- gh -----------------------------------------------------------------------------------

    def run_gh(self, bucket: str, *args: str) -> str:
        found = self.repo_dir_for_bucket(bucket)
        if found is None:
            raise Unavailable(f"no repo owns the plans bucket `{bucket}`")
        try:
            result = subprocess.run(
                [self.gh, *args], cwd=found[0], text=True, capture_output=True, check=False, timeout=GH_TIMEOUT
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Unavailable(f"gh could not run for {found[1]}: {exc}") from exc
        if result.returncode != 0:
            raise Unavailable(f"gh {' '.join(args[:2])} failed for {found[1]}: {(result.stderr or result.stdout).strip()}")
        return result.stdout

    def issue_states(self, bucket: str) -> dict[int, str]:
        if bucket not in self._states:
            slug = self.slug_for(bucket)
            out = self.run_gh(bucket, "issue", "list", "--repo", slug, "--state", "all", "--limit", "5000", "--json", "number,state")
            try:
                self._states[bucket] = {int(row["number"]): str(row["state"]).upper() for row in json.loads(out or "[]")}
            except (ValueError, KeyError, TypeError) as exc:
                raise Unavailable(f"unparsable issue list for {bucket}: {exc}") from exc
        return self._states[bucket]

    def pr_held(self, bucket: str) -> dict[int, int]:
        """issue number -> number of an OPEN PR that will close it on merge (none for a no-PR bucket)."""
        if bucket in self.ctx.cfg["fixed_buckets"].values():
            return {}
        out = self.run_gh(bucket, "pr", "list", "--repo", self.slug_for(bucket), "--state", "open", "--limit", "500", "--json", "number,closingIssuesReferences")
        held: dict[int, int] = {}
        try:
            for pr in json.loads(out or "[]"):
                for ref in pr.get("closingIssuesReferences") or []:
                    held.setdefault(int(ref["number"]), int(pr["number"]))
        except (ValueError, KeyError, TypeError) as exc:
            raise Unavailable(f"unparsable PR list for {bucket}: {exc}") from exc
        return held

    def merged_evidence(self, bucket: str) -> tuple[set[str], set[int]] | None:
        """(head branches, closing-reference issue numbers) of MERGED PRs; None for a no-PR bucket."""
        if bucket in self.ctx.cfg["fixed_buckets"].values():
            return None
        out = self.run_gh(bucket, "pr", "list", "--repo", self.slug_for(bucket), "--state", "merged", "--limit", "1000", "--json", "number,headRefName,closingIssuesReferences")
        branches: set[str] = set()
        issues: set[int] = set()
        try:
            for pr in json.loads(out or "[]"):
                if pr.get("headRefName"):
                    branches.add(str(pr["headRefName"]))
                issues.update(int(ref["number"]) for ref in pr.get("closingIssuesReferences") or [])
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise Unavailable(f"unparsable merged PR list for {bucket}: {exc}") from exc
        return branches, issues

    def close_issue(self, bucket: str, number: int, why: str) -> None:
        self.run_gh(bucket, "issue", "close", str(number), "--repo", self.slug_for(bucket), "--reason", "completed", "--comment", why)
        self._states.get(bucket, {})[number] = "CLOSED"

    # --- moves --------------------------------------------------------------------------------

    def move(self, src: Path, dst: Path) -> None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        code, _ = base.git(self.root, "mv", str(src), str(dst))
        if code != 0:
            src.rename(dst)


def close_for_plans(
    store: Store, bucket: str, plans: list[tuple[Path, int]], dry_run: bool, ignore_topic: str | None = None
) -> tuple[int, int]:
    """Close the open issue of each (plan, number); returns (closed, failed)."""
    states = store.issue_states(bucket)
    held_open = store.open_issue_numbers(bucket, ignore_topic)
    held_by_pr = store.pr_held(bucket)
    merged = store.merged_evidence(bucket)
    closed = failed = 0
    done: set[int] = set()
    for plan, number in plans:
        if number in done:
            continue
        done.add(number)
        state = states.get(number)
        if state is None:
            print(f"issue    #{number} not found ({plan.relative_to(store.root)})")
            failed += 1
        elif state != "OPEN":
            print(f"issue    #{number} already {state.lower()}")
        elif number in held_open:
            print(f"issue    #{number} left open: a plan still in open/ names it")
        elif number in held_by_pr:
            print(f"issue    #{number} left open: PR #{held_by_pr[number]} will close it on merge")
        elif merged is not None and number not in merged[1] and wf_frontmatter.parse(plan).get("branch", "") not in merged[0]:
            print(f"issue    #{number} left open: no merged PR found for its branch or issue")
        elif dry_run:
            print(f"issue    #{number} would be closed ({plan.parent.parent.name}/{plan.parent.name}/{plan.stem})")
        else:
            why = f"Closing: plan `{plan.parent.name}/{plan.stem}` is finished and archived under plans/{bucket}/completed/."
            try:
                store.close_issue(bucket, number, why)
                print(f"issue    #{number} closed")
                closed += 1
            except Unavailable as exc:
                print(f"issue    #{number} NOT closed: {exc}", file=sys.stderr)
                failed += 1
    return closed, failed


def completed_plans(store: Store, bucket: str, topic_name: str | None) -> list[tuple[Path, int]]:
    out: list[tuple[Path, int]] = []
    for topic in store.topics(bucket, "completed"):
        if topic_name and topic.name != topic_name:
            continue
        for plan in store.plan_files(topic):
            if wf_frontmatter.parse(plan).get("finished") is False:
                continue
            number = store.issue_of(plan, bucket)
            if number is not None:
                out.append((plan, number))
    return out


# --- commands ---------------------------------------------------------------------------------


def cmd_complete(store: Store, args: argparse.Namespace) -> int:
    bucket = store.bucket_for(args.repo)
    src = store.plans / bucket / "open" / args.topic
    dst = store.plans / bucket / "completed" / args.topic
    if not src.is_dir():
        raise Unavailable(f"no open topic {src.relative_to(store.root)}")
    if dst.exists():
        raise Unavailable(f"{dst.relative_to(store.root)} already exists; merge by hand")
    plans = store.plan_files(src)
    states = {p: wf_frontmatter.parse(p).get("finished") for p in plans}
    unfinished = [p.name for p, v in states.items() if v is False]
    if unfinished:
        print("not finishable: finished: false in " + ", ".join(unfinished), file=sys.stderr)
        return UNFINISHED
    if not any(v is True for v in states.values()):
        print("not finishable: no plan in the topic says `finished: true`", file=sys.stderr)
        return UNFINISHED
    for p, v in states.items():
        if v is None:
            print(f"note     {p.name} has no `finished:` line; ignored")
    pairs = [(dst / p.name, n) for p in plans if (n := store.issue_of(p, bucket)) is not None]
    if args.dry_run:
        print(f"would move {src.relative_to(store.root)} -> {dst.relative_to(store.root)}")
    else:
        store.move(src, dst)
        print(f"moved    {src.relative_to(store.root)} -> {dst.relative_to(store.root)}")
    no_pr_bucket = bucket in store.ctx.cfg["fixed_buckets"].values()
    if pairs and not (no_pr_bucket or args.close_issues):
        print("note     issues are closed by the PR's `Closes #N` on merge; run `close-issues` afterwards for any left open")
    elif pairs:
        try:
            _, failed = close_for_plans(store, bucket, pairs, args.dry_run, ignore_topic=args.topic)
        except Unavailable as exc:
            print(f"plan-lifecycle: {exc}", file=sys.stderr)
            failed = 1
        if failed:
            print(f"moved, but issue closing failed: rerun `plan-lifecycle.sh close-issues {args.repo} {args.topic}`", file=sys.stderr)
            return UNAVAILABLE
    return OK


def cmd_close_issues(store: Store, args: argparse.Namespace) -> int:
    bucket = store.bucket_for(args.repo)
    pairs = completed_plans(store, bucket, args.topic)
    if not pairs:
        print("no completed plans with a github_issue")
        return OK
    _, failed = close_for_plans(store, bucket, pairs, args.dry_run)
    return UNAVAILABLE if failed else OK


PLAN_IN_PROGRESS_DIRS = ("research", "mocks", "mockup", "mockups")


def has_files(directory: Path) -> bool:
    return directory.is_dir() and any(p.is_file() for p in directory.rglob("*"))


def is_review_only(topic: Path) -> bool:
    """No plan file, a push review on disk, and no research/mocks (those mean a plan is still being written)."""
    return (
        not Store.topic_markdown(topic)
        and has_files(topic / "reviews")
        and not any(has_files(topic / name) for name in PLAN_IN_PROGRESS_DIRS)
    )


def orphans(store: Store, bucket: str) -> list[Path]:
    return [t for t in store.topics(bucket, "open") if is_review_only(t)]


def cmd_audit(store: Store, args: argparse.Namespace) -> int:
    drift = unavailable = 0
    for bucket in store.buckets(args.repo):
        for topic in orphans(store, bucket):
            print(f"ORPHAN        {bucket}: open/{topic.name} is review-only, no plan (sweep it to completed/)")
            drift += 1
        wanted = [(p, n, "completed") for p, n in completed_plans(store, bucket, None)]
        wanted += [(p, n, "open") for t in store.topics(bucket, "open") for p in store.plan_files(t) if (n := store.issue_of(p, bucket)) is not None]
        if not wanted:
            continue
        try:
            states = store.issue_states(bucket)
        except Unavailable as exc:
            print(f"UNAVAILABLE   {bucket}: {exc}")
            unavailable += 1
            continue
        held_open = store.open_issue_numbers(bucket)
        try:
            held_by_pr = store.pr_held(bucket)
        except Unavailable as exc:
            print(f"UNAVAILABLE   {bucket}: {exc}")
            unavailable += 1
            continue
        for plan, number, state in wanted:
            where = f"{bucket}: {state}/{plan.parent.name}/{plan.stem} -> #{number}"
            issue = states.get(number)
            if issue is None:
                print(f"ISSUE-MISSING {where} not found in the repo")
            elif state == "completed" and issue == "OPEN" and number in held_by_pr:
                print(f"ISSUE-PENDING {where} awaits PR #{held_by_pr[number]}")
            elif state == "completed" and issue == "OPEN" and number not in held_open:
                print(f"ISSUE-OPEN    {where} is still open")
                drift += 1
            elif state == "open" and issue == "CLOSED":
                print(f"ISSUE-CLOSED  {where} is closed but the plan is still open")
    print(f"audit: {drift} drift finding(s), {unavailable} bucket(s) unavailable")
    return DRIFT if drift else (UNAVAILABLE if unavailable else OK)


def cmd_sweep(store: Store, args: argparse.Namespace) -> int:
    skipped = 0
    for bucket in store.buckets(args.repo):
        for topic in orphans(store, bucket):
            dst = store.plans / bucket / "completed" / topic.name
            if dst.exists():
                print(f"skipped  {topic.relative_to(store.root)}: {dst.relative_to(store.root)} exists", file=sys.stderr)
                skipped += 1
            elif args.dry_run:
                print(f"would move {topic.relative_to(store.root)} -> {dst.relative_to(store.root)}")
            else:
                store.move(topic, dst)
                print(f"moved    {topic.relative_to(store.root)} -> {dst.relative_to(store.root)}")
    return UNAVAILABLE if skipped else OK


def cmd_review_topic(store: Store, args: argparse.Namespace) -> int:
    bucket = store.bucket_for(args.repo)
    if not re.match(r"^[a-z0-9][a-z0-9._-]*$", args.name):
        print(f"review-topic: invalid topic name `{args.name}`", file=sys.stderr)
        return USAGE
    for state in STATES:
        existing = store.plans / bucket / state / args.name
        if existing.is_dir():
            (existing / "reviews").mkdir(exist_ok=True)
            print(existing)
            return OK
    target = store.plans / bucket / "completed" / args.name
    (target / "reviews").mkdir(parents=True)
    (target / "tmp").mkdir()
    print(target)
    return OK


# --- reconcile --------------------------------------------------------------------------------

UNTICKED_RE = re.compile(r"^[ \t]*- \[ \]")
MARKER_RE = re.compile(r"^([ \t]*- \[)[ xX~]")
NON_BRANCHES = ("", "main", "none")


def split_pr_numbers(plan: Path) -> list[int]:
    """PR numbers in a plan's `split_into:` frontmatter key (inline list or block of `pr:`/bare items)."""
    lines = plan.read_text().splitlines()
    if not lines or lines[0].rstrip() != "---":
        return []
    chunk: list[str] = []
    capturing = False
    for line in lines[1:]:
        if line.rstrip() == "---":
            break
        if line.startswith("split_into:"):
            capturing = True
            chunk.append(line.split(":", 1)[1])
        elif capturing and line[:1] in (" ", "\t"):
            chunk.append(line)
        else:
            capturing = False
    text = "\n".join(chunk)
    keyed = re.findall(r"\bpr:\s*(\d+)", text)
    return sorted({int(n) for n in (keyed or re.findall(r"\d+", text))})


def pr_label(numbers: list[int]) -> str:
    if len(numbers) == 1:
        return f"PR #{numbers[0]}"
    if numbers == list(range(numbers[0], numbers[-1] + 1)):
        return f"PRs #{numbers[0]}–#{numbers[-1]}"
    return "PRs " + ", ".join(f"#{n}" for n in numbers)


def field_line(lines: list[str], step: plan_parse.Step, regex: re.Pattern) -> int | None:
    """1-based line of the first `regex` match inside the step body, outside fenced code blocks."""
    fence: str | None = None
    for number in range(step.start + 1, min(step.end, len(lines)) + 1):
        line = lines[number - 1]
        fenced = plan_parse.FENCE_RE.match(line)
        if fence is not None:
            if fenced and fenced.group(1)[0] == fence[0] and len(fenced.group(1)) >= len(fence):
                fence = None
            continue
        if fenced:
            fence = fenced.group(1)
            continue
        if regex.match(line):
            return number
    return None


class Reconciler:
    def __init__(self, store: Store, bucket: str, merged: bool, dry_run: bool) -> None:
        self.store = store
        self.bucket = bucket
        self.merged = merged
        self.dry_run = dry_run
        self.no_pr = bucket in store.ctx.cfg["fixed_buckets"].values()
        self.matched = 0
        self.lookups_ok = 0
        self.lookups_failed = 0
        self.today = datetime.date.today().isoformat()
        self._sub_lines: dict[Path, list[str]] = {}
        self._sub_dirty: set[Path] = set()
        self._lookups: dict[str, dict | None] = {}

    def rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.store.root))
        except ValueError:
            return str(path)

    def tick(self, lines: list[str], path: Path, box: plan_parse.Box) -> bool:
        if not UNTICKED_RE.match(lines[box.line - 1]):
            return False
        lines[box.line - 1] = MARKER_RE.sub(r"\1x", lines[box.line - 1], count=1)
        verb = "would tick" if self.dry_run else "ticked    "
        print(f"{verb}{' ' if self.dry_run else ''}{self.rel(path)}:{box.line} {box.text}")
        return True

    def sub_lines(self, path: Path) -> list[str]:
        if path not in self._sub_lines:
            self._sub_lines[path] = path.read_text(encoding="utf-8").splitlines()
        return self._sub_lines[path]

    def find_sub(self, master: Path, step: plan_parse.Step) -> Path | None:
        link = step.sub_plan_link
        if link and link != "none":
            found = plan_parse.resolve_link(master, link)
            if found.kind == "dangling":
                print(f"note     {self.rel(master)}: step {step.n} sub-plan link `{link}` is dangling; left untouched")
                return None
            return found.path if found.path is not None and found.path.is_file() else None
        branch = step.branch
        if branch is None or branch in NON_BRANCHES:
            return None
        for candidate in Store.plan_files(master.parent):
            if candidate != master and plan_parse.parse_plan(candidate).frontmatter.get("branch") == branch:
                return candidate
        return None

    def merged_pr(self, branch: str) -> dict | None:
        if branch in self._lookups:
            return self._lookups[branch]
        found: dict | None = None
        try:
            out = self.store.run_gh(
                self.bucket, "pr", "list", "--repo", self.store.slug_for(self.bucket), "--head", branch,
                "--state", "merged", "--limit", "20", "--json", "number,mergedAt,mergeCommit",
            )
            rows = [r for r in json.loads(out or "[]") if r.get("mergedAt")]
            found = max(rows, key=lambda r: str(r["mergedAt"])) if rows else None
            self.lookups_ok += 1
        except (Unavailable, ValueError, TypeError, AttributeError) as exc:
            print(f"note     merged-PR lookup for `{branch}` failed: {exc}", file=sys.stderr)
            self.lookups_failed += 1
        self._lookups[branch] = found
        return found

    def merged_split(self, prs: list[int]) -> dict | None:
        key = "split:" + ",".join(map(str, prs))
        if key in self._lookups:
            return self._lookups[key]
        found: dict | None = None
        try:
            rows = []
            for number in prs:
                out = self.store.run_gh(
                    self.bucket, "pr", "view", str(number), "--repo", self.store.slug_for(self.bucket),
                    "--json", "number,mergedAt,mergeCommit",
                )
                rows.append(json.loads(out or "{}"))
            if rows and all(r.get("mergedAt") for r in rows):
                last = max(rows, key=lambda r: str(r["mergedAt"]))
                found = {**last, "label": pr_label(sorted(prs))}
            self.lookups_ok += 1
        except (Unavailable, ValueError, TypeError, AttributeError) as exc:
            print(f"note     split-PR lookup for {prs} failed: {exc}", file=sys.stderr)
            self.lookups_failed += 1
        self._lookups[key] = found
        return found

    def convert_post_merge(self, lines: list[str], path: Path, boxes: list[plan_parse.Box]) -> bool:
        changed = False
        for box in boxes:
            if box.state == "post-merge" and self.tick(lines, path, box):
                changed = True
        return changed

    def set_status(self, lines: list[str], master: Path, step: plan_parse.Step, value: str) -> None:
        text = f"**Status:** {value}"
        number = field_line(lines, step, plan_parse.STATUS_RE)
        if number is not None:
            lines[number - 1] = text
        else:
            after = field_line(lines, step, plan_parse.BRANCH_RE) or field_line(lines, step, plan_parse.SUBPLAN_RE) or step.start
            lines.insert(after, text)
            number = after + 1
        verb = "would set " if self.dry_run else "status    "
        print(f"{verb}{self.rel(master)}:{number} {value}")

    def reconcile_step(self, master: Path, step: plan_parse.Step, lines: list[str]) -> bool:
        sub = self.find_sub(master, step)
        if sub is None:
            return False
        sub_doc = plan_parse.parse_plan(sub)
        if sub_doc.finished is not True:
            return False
        sub_boxes = [b for s in sub_doc.steps for b in s.boxes] + sub_doc.stray_boxes
        current = step.status
        new_status: str | None = None
        if current is None or current.startswith(("todo", "in-progress")):
            new_status = "done" if self.no_pr else f"done — awaiting merge ({self.today})"
        effective = new_status or current or ""
        convert = self.no_pr
        if self.merged and not self.no_pr and effective.startswith(("done", "merged")):
            settled = effective.startswith("merged") and not any(
                b.state == "post-merge" for b in [*step.boxes, *sub_boxes]
            )
            branch = str(sub_doc.frontmatter.get("branch") or step.branch or "")
            split_prs = split_pr_numbers(sub)
            if settled:
                hit = None
            elif split_prs:
                hit = self.merged_split(split_prs)
            else:
                hit = None if branch in NON_BRANCHES else self.merged_pr(branch)
            if hit is not None:
                convert = True
                if effective.startswith("done"):
                    commit = (hit.get("mergeCommit") or {}).get("oid") or ""
                    sha = f", {commit[:7]}" if commit else ""
                    label = hit.get("label") or f"PR #{hit['number']}"
                    new_status = f"merged — {label} ({str(hit['mergedAt'])[:10]}{sha})"
        changed = False
        for box in step.boxes:
            if box.state == "open" and self.tick(lines, master, box):
                changed = True
        if convert:
            changed = self.convert_post_merge(lines, master, step.boxes) or changed
            sub_lines = self.sub_lines(sub)
            if self.convert_post_merge(sub_lines, sub, sub_boxes):
                self._sub_dirty.add(sub)
        if new_status is not None:
            self.set_status(lines, master, step, new_status)
            changed = True
        return changed

    def reconcile_master(self, master: Path) -> None:
        doc = plan_parse.parse_plan(master)
        if doc.effective_type == "orchestrator":
            print(f"note     {self.rel(master)}: orchestrator master ignored")
            return
        if not (doc.type == "master" or (doc.type is None and "master" in master.stem.lower())):
            return
        if doc.legacy:
            print(f"note     {self.rel(master)}: legacy plan ignored")
            return
        text = master.read_text(encoding="utf-8")
        lines = text.splitlines()
        changed = False
        # Last step first: a Status line inserted in one step must not shift the line numbers of earlier steps.
        for step in reversed(doc.steps):
            if step.sub_plan_link is None and step.branch is None:
                continue
            self.matched += 1
            if self.reconcile_step(master, step, lines):
                changed = True
        if changed and not self.dry_run:
            master.write_text("\n".join(lines) + ("\n" if text.endswith("\n") else ""), encoding="utf-8")

    def flush(self) -> None:
        if self.dry_run:
            return
        for path in self._sub_dirty:
            original = path.read_text(encoding="utf-8")
            path.write_text("\n".join(self._sub_lines[path]) + ("\n" if original.endswith("\n") else ""), encoding="utf-8")


def cmd_reconcile(store: Store, args: argparse.Namespace) -> int:
    bucket = store.bucket_for(args.repo)
    rec = Reconciler(store, bucket, args.merged, args.dry_run)
    for state in (STATES if args.merged else STATES[:1]):
        for topic in store.topics(bucket, state):
            if args.topic and topic.name != args.topic:
                continue
            for plan in store.plan_files(topic):
                rec.reconcile_master(plan)
    rec.flush()
    if args.topic and rec.matched == 0:
        print(f"note     no master step found for topic `{args.topic}`", file=sys.stderr)
        return DRIFT
    if rec.lookups_failed and not rec.lookups_ok:
        return UNAVAILABLE
    return OK


def cmd_backfill(store: Store, args: argparse.Namespace) -> int:
    rules = (args.only,) if args.only else plan_backfill.RULES
    return plan_backfill.Backfill(store, rules, args.dry_run, args.force).run()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="plan-lifecycle.sh", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stronghold", default=os.environ.get("STRONGHOLD_DIR") or str(LIB_DIR.parent.parent.parent))
    sub = parser.add_subparsers(dest="command", required=True)
    complete = sub.add_parser("complete")
    complete.add_argument("repo")
    complete.add_argument("topic")
    complete.add_argument("--dry-run", action="store_true")
    complete.add_argument("--close-issues", action="store_true")
    close = sub.add_parser("close-issues")
    close.add_argument("repo")
    close.add_argument("topic", nargs="?")
    close.add_argument("--dry-run", action="store_true")
    audit = sub.add_parser("audit")
    audit.add_argument("repo", nargs="?")
    sweep = sub.add_parser("sweep")
    sweep.add_argument("repo", nargs="?")
    sweep.add_argument("--dry-run", action="store_true")
    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument("repo")
    reconcile.add_argument("topic", nargs="?")
    reconcile.add_argument("--merged", action="store_true")
    reconcile.add_argument("--dry-run", action="store_true")
    backfill = sub.add_parser("backfill")
    backfill.add_argument("--dry-run", action="store_true")
    backfill.add_argument("--only", choices=plan_backfill.RULES)
    backfill.add_argument("--force", action="store_true")
    review = sub.add_parser("review-topic")
    review.add_argument("repo")
    review.add_argument("name")
    return parser


COMMANDS = {
    "complete": cmd_complete,
    "close-issues": cmd_close_issues,
    "audit": cmd_audit,
    "sweep": cmd_sweep,
    "reconcile": cmd_reconcile,
    "backfill": cmd_backfill,
    "review-topic": cmd_review_topic,
}


def main(argv: list[str]) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return USAGE if exc.code not in (0, None) else OK
    store = Store(Path(args.stronghold).resolve())
    try:
        return COMMANDS[args.command](store, args)
    except Unavailable as exc:
        print(f"plan-lifecycle: {exc}", file=sys.stderr)
        return UNAVAILABLE


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
