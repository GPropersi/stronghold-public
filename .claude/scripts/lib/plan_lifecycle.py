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
                                        left open.
  audit [<repo>]                        report drift, exit 1 when any: a completed plan whose issue is still open
                                        (ISSUE-OPEN) and a review-only open/ topic: no plan file, a push review, no research/mocks (ORPHAN). Info only: a
                                        closed issue on a still-open plan (ISSUE-CLOSED), an open issue awaiting an open
                                        PR (ISSUE-PENDING).
  sweep [<repo>] [--dry-run]            `git mv` every ORPHAN open/ topic to completed/ (skipped when completed/<topic> exists).
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
import json
import os
import re
import subprocess
import sys
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB_DIR))

import wf_frontmatter  # noqa: E402
import wf_lint_base as base  # noqa: E402

STATES = ("open", "completed")
NUMBER_RE = re.compile(r"^[0-9]+$")
SLUG_RE = re.compile(r"[:/]([^/:]+/[^/]+?)(?:\.git)?/?$")
ISSUE_URL_RE = re.compile(r"github\.com/([^/\s]+/[^/\s]+)/(?:issues|pull)/[0-9]+")
GH_TIMEOUT = 90

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
    def plan_files(topic: Path) -> list[Path]:
        return sorted(p for p in topic.glob("*.md") if p.is_file())

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
        not Store.plan_files(topic)
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
    review = sub.add_parser("review-topic")
    review.add_argument("repo")
    review.add_argument("name")
    return parser


COMMANDS = {
    "complete": cmd_complete,
    "close-issues": cmd_close_issues,
    "audit": cmd_audit,
    "sweep": cmd_sweep,
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
