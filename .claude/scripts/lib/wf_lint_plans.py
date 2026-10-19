"""Plans group of the workflow lint: frontmatter, lifecycle and layout of <root>/plans."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import plan_parse
import wf_frontmatter
import wf_lint_base as base
import plan_lifecycle

STATES = ("open", "completed")
BUCKET_ENTRIES = {"open", "completed", "research"}
TOPIC_FOLDERS = {"reviews", "research", "mocks", "mockup", "mockups", "tmp", "screenshots"}
WT_RESOLVE = Path(__file__).resolve().parent.parent / "wt-resolve.sh"

PLAN_TYPES = frozenset({"plan", "master", "orchestrator"})
ALL_TYPES = PLAN_TYPES | plan_lifecycle.NON_PLAN_TYPES
ALLOWED_TYPES_TEXT = "|".join(sorted(ALL_TYPES))
STATUS_VOCAB = frozenset({"todo", "in-progress", "done", "merged", "moved"})
# The eleven plan-schema rules are binding (the backfill landed); plans.github-issue-consistent stays a permanent warn.
PROMOTED_IDS = frozenset({
    "plans.type-required", "plans.finished-in-status", "plans.steps-parseable", "plans.master-step-fields",
    "plans.link-resolves", "plans.completed-has-open-boxes", "plans.checkbox-grammar",
    "plans.progress-tracking-retired", "plans.frontmatter-complete", "plans.github-issue-required",
    "plans.topic-root-file-type",
})
BOX_VARIANT_RE = re.compile(r"^[ \t]*([*+-])([ \t]+)\[([ xX~])\]")


def _severity(check_id: str) -> str:
    """`error` for the eleven promoted plan-schema rules; every other plans.* rule here stays `warn`."""
    return "error" if check_id in PROMOTED_IDS else "warn"


def _topics(bucket_dir: Path, state: str) -> list[Path]:
    state_dir = bucket_dir / state
    try:
        return sorted(p for p in state_dir.iterdir() if p.is_dir())
    except OSError:
        return []


def _plan_files(topic: Path) -> list[Path]:
    """Topic-root markdown that counts as a plan (same predicate as plan-lifecycle)."""
    return plan_lifecycle.Store.plan_files(topic)


def _confirmed(ctx: base.Context, repo: str, branch: str, worktree: str) -> bool | None:
    """True/False from wt-resolve.sh --branch --worktree (stale-worktree absent/present); None if unknown."""
    env = {**os.environ, "STRONGHOLD_DIR": str(ctx.root), "HOME": str(ctx.home)}
    if branch.startswith("-") or any(c.isspace() or ord(c) < 32 for c in branch):
        return None
    try:
        result = subprocess.run(
            ["bash", str(WT_RESOLVE), repo, "--branch", branch, "--worktree", worktree],
            text=True, capture_output=True, env=env, check=False, timeout=30,
        )
        return "stale-worktree" not in json.loads(result.stdout)["problems"]
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
        return None


def _check_plan(ctx: base.Context, bucket: str, state: str, plan: Path) -> list[base.Finding]:
    out: list[base.Finding] = []
    meta = wf_frontmatter.parse(plan)
    location = ctx.rel(plan)
    repo = ctx.repo_for_bucket(bucket)
    if "repo" not in meta:
        out.append(base.Finding("warn", "plans.frontmatter-repo-matches-bucket", location,
                                f"plan has no `repo:` frontmatter (expected `{bucket}`)", repo))
    elif meta["repo"] != bucket:
        out.append(base.Finding("error", "plans.frontmatter-repo-matches-bucket", location,
                                f"`repo: {meta['repo']}` does not match the bucket directory plans/{bucket}/", repo))
    branch = meta.get("branch", "")
    worktree = meta.get("worktree", "")
    if bucket in ctx.cfg["fixed_buckets"].values():
        if branch:
            out.append(base.Finding("error", "plans.branch-absent-for-stronghold", location,
                                    "stronghold plans push to main and carry no `branch:`"))
    elif branch and repo:
        default = ctx.seam(repo).token("Default branch")
        if default and branch == default:
            out.append(base.Finding("error", "plans.branch-not-default", location,
                                    f"`branch: {branch}` is the repo's default branch (omit it for default-branch plans)", repo))
    if worktree:
        pattern = rf"^{re.escape(repo)}/\.claude/worktrees/[a-z0-9-]+$" if repo else None
        valid = pattern is not None and re.match(pattern, worktree) is not None
        if not valid:
            out.append(base.Finding("error", "plans.worktree-format", location,
                                    f"`worktree: {worktree}` must look like <repo>/.claude/worktrees/<slug>", repo))
        if not branch:
            out.append(base.Finding("error", "plans.worktree-implies-branch", location,
                                    "`worktree:` is set without `branch:`", repo))
        elif repo and valid and _confirmed(ctx, repo, branch, worktree) is False:
            out.append(base.Finding("warn", "plans.worktree-confirmed-by-porcelain", location,
                                    f"`worktree: {worktree}` is not on branch {branch} in `git worktree list` (cache is stale)", repo))
    if state == "completed":
        finished = meta.get("finished")
        if finished is False:
            out.append(base.Finding("error", "plans.completed-has-finished-true", location,
                                    "plan lives in completed/ but its Status says `finished: false`", repo))
        elif finished is None:
            out.append(base.Finding("warn", "plans.completed-has-finished-true", location,
                                    "plan lives in completed/ but has no `finished:` line in its Status block", repo))
    return out


def _is_master(plan: Path, doc: plan_parse.PlanDoc) -> bool:
    """A master is `type: master`, or (untyped) a file named `*-master.md`."""
    return doc.type == "master" or (doc.type is None and plan.stem.endswith("-master"))


def _bucket_slug(store: plan_lifecycle.Store, bucket: str) -> str | None:
    """The bucket's `Repo slug`; None when unavailable (the URL half of github-issue-consistent is then skipped)."""
    try:
        return store.slug_for(bucket)
    except plan_lifecycle.Unavailable:
        return None


def _grammar_variants(plan: Path) -> int:
    """Count checkbox lines outside fences and `## Progress Tracking` that are not `- [ ]`/`- [x]`/`- [~]`."""
    try:
        lines = plan.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return 0
    count = 0
    fence: str | None = None
    section = ""
    for line in lines:
        fenced = plan_parse.FENCE_RE.match(line)
        if fence is not None:
            if fenced and fenced.group(1)[0] == fence[0] and len(fenced.group(1)) >= len(fence):
                fence = None
            continue
        if fenced:
            fence = fenced.group(1)
            continue
        if plan_parse.H2_RE.match(line):
            section = line[2:].strip().lower()
            continue
        if section == "progress tracking":
            continue
        found = BOX_VARIANT_RE.match(line)
        if found and (found.group(1) != "-" or found.group(2) != " " or found.group(3) == "X"):
            count += 1
    return count


def _check_hygiene(
    ctx: base.Context, bucket: str, state: str, plan: Path, doc: plan_parse.PlanDoc,
    slug: str | None, master_issues: set[int],
) -> list[base.Finding]:
    """Schema rules for one plan/master/orchestrator doc (severity per `_severity`)."""
    out: list[base.Finding] = []
    location = ctx.rel(plan)
    repo = ctx.repo_for_bucket(bucket)
    master = _is_master(plan, doc)

    def warn(check_id: str, message: str) -> None:
        out.append(base.Finding(_severity(check_id), check_id, location, message, repo))

    if doc.finished_in_frontmatter:
        warn("plans.finished-in-status", "`finished:` is set in the frontmatter: it belongs under `## Status`")
    elif doc.finished is None:
        warn("plans.finished-in-status", "no `finished:` line under `## Status`")
    if not doc.steps and not doc.legacy:
        warn("plans.steps-parseable", "no parseable `### N. Title` steps (add steps, or `legacy: true` for an older phase-style plan)")
    if master and doc.type != "orchestrator":
        bad = 0
        for step in doc.steps:
            words = (step.status or "").split(None, 1)
            if step.sub_plan_link is None or step.branch is None or not words or words[0] not in STATUS_VOCAB:
                bad += 1
        if bad:
            warn("plans.master-step-fields",
                 f"{bad} master step(s) missing Sub-plan/Branch/Status or with a Status outside {'|'.join(sorted(STATUS_VOCAB))}")
    if master:
        kinds: dict[str, int] = {}
        for step in doc.steps:
            if step.sub_plan_link not in (None, "none"):
                resolved = plan_parse.resolve_link(plan, step.sub_plan_link)
                if resolved.kind != "ok":
                    kinds[resolved.kind] = kinds.get(resolved.kind, 0) + 1
        if kinds:
            detail = ", ".join(f"{count} {kind}" for kind, count in sorted(kinds.items()))
            warn("plans.link-resolves", f"{sum(kinds.values())} sub-plan link(s) do not resolve ({detail})")
    if state == "completed":
        boxes = [b for step in doc.steps for b in step.boxes] + doc.stray_boxes
        unticked = sum(1 for b in boxes if b.state == "open")
        if unticked:
            warn("plans.completed-has-open-boxes", f"completed plan has {unticked} unticked `- [ ]` box(es)")
    variants = _grammar_variants(plan)
    if variants:
        warn("plans.checkbox-grammar", f"{variants} checkbox line(s) are not `- [ ]`/`- [x]`/`- [~]`")
    if state == "open" and doc.progress_tracking_present:
        warn("plans.progress-tracking-retired", "`## Progress Tracking` is retired in open plans: track progress with the step boxes")
    if "repo" not in doc.frontmatter:
        warn("plans.frontmatter-complete", "frontmatter has no `repo:`")
    if doc.github_issue is None:
        warn("plans.github-issue-required", "no `github_issue:` (use the issue number, or `github_issue: none` as the explicit waiver)")
    elif doc.github_issue == "invalid":
        warn("plans.github-issue-required",
             f"invalid github_issue `{doc.github_issue_raw}` (use the issue number, or `none` as the explicit waiver)")
    problems: list[str] = []
    match = plan_lifecycle.ISSUE_URL_RE.search(doc.github_issue_url or "")
    if match and slug is not None and match.group(1).lower() != slug.lower():
        problems.append(f"`github_issue_url` names {match.group(1)}, not the bucket's {slug}")
    if not master and doc.effective_type == "plan" and isinstance(doc.github_issue, int) and doc.github_issue in master_issues:
        problems.append(f"`github_issue: {doc.github_issue}` is its master's issue: a sub-plan owns its own issue")
    if problems:
        warn("plans.github-issue-consistent", "; ".join(problems))
    return out


def _check_topic_files(ctx: base.Context, bucket: str, state: str, topic: Path, store: plan_lifecycle.Store) -> list[base.Finding]:
    out: list[base.Finding] = []
    repo = ctx.repo_for_bucket(bucket)
    for entry in sorted(p for p in topic.iterdir() if p.is_file() and not p.name.startswith(".") and p.suffix != ".md"):
        out.append(base.Finding(_severity("plans.topic-root-file-type"), "plans.topic-root-file-type", ctx.rel(entry),
                                "non-markdown file at the topic root (plans, masters and docs are `.md`; keep other files in mocks/ or research/)", repo))
    slug = _bucket_slug(store, bucket)
    docs = {p: plan_parse.parse_plan(p) for p in plan_lifecycle.Store.topic_markdown(topic)}
    master_issues = {d.github_issue for p, d in docs.items() if _is_master(p, d) and isinstance(d.github_issue, int)}
    for plan, doc in docs.items():
        if doc.type is None:
            out.append(base.Finding(_severity("plans.type-required"), "plans.type-required", ctx.rel(plan),
                                    f"no `type:` frontmatter (allowed: {ALLOWED_TYPES_TEXT})", repo))
        elif doc.type not in ALL_TYPES:
            out.append(base.Finding(_severity("plans.type-required"), "plans.type-required", ctx.rel(plan),
                                    f"unrecognised `type: {doc.type}` (allowed: {ALLOWED_TYPES_TEXT})", repo))
        if doc.type in plan_lifecycle.NON_PLAN_TYPES:
            continue
        out.extend(_check_hygiene(ctx, bucket, state, plan, doc, slug, master_issues))
    return out


def _check_bucket(ctx: base.Context, bucket_dir: Path) -> list[base.Finding]:
    out: list[base.Finding] = []
    bucket = bucket_dir.name
    repo = ctx.repo_for_bucket(bucket)
    store = plan_lifecycle.Store(ctx.root)
    store.ctx = ctx
    for entry in sorted(bucket_dir.iterdir()):
        if entry.name not in BUCKET_ENTRIES:
            out.append(base.Finding("warn", "plans.layout-valid", ctx.rel(entry),
                                    "unexpected entry in a plans bucket (expected open/ completed/ research/)", repo))
    names: dict[str, set[str]] = {}
    for state in STATES:
        state_dir = bucket_dir / state
        if state_dir.is_dir():
            for entry in sorted(state_dir.iterdir()):
                if entry.is_file() and entry.name != ".gitkeep":
                    out.append(base.Finding("warn", "plans.layout-valid", ctx.rel(entry),
                                            f"loose file in {state}/ (plans live in {state}/<topic>/)", repo))
        for topic in _topics(bucket_dir, state):
            names.setdefault(topic.name, set()).add(state)
            for sub in sorted(p for p in topic.iterdir() if p.is_dir()):
                if sub.name not in TOPIC_FOLDERS:
                    out.append(base.Finding("warn", "plans.layout-valid", ctx.rel(sub),
                                            "unexpected folder in a topic (expected reviews/ research/ mocks/ tmp/ screenshots/)", repo))
            plans = _plan_files(topic)
            for plan in plans:
                out.extend(_check_plan(ctx, bucket, state, plan))
            out.extend(_check_topic_files(ctx, bucket, state, topic, store))
            if state == "open" and plan_lifecycle.is_review_only(topic):
                out.append(base.Finding("warn", "plans.open-topic-has-plan", ctx.rel(topic),
                                        "open topic is review-only, no plan (it belongs in completed/): run `make plan-sweep`", repo))
            if state == "open" and plans and all(wf_frontmatter.parse(p).get("finished") is True for p in plans):
                out.append(base.Finding("warn", "plans.open-topic-fully-finished", ctx.rel(topic),
                                        "every plan in this open topic is finished: move it to completed/", repo))
    for topic, states in sorted(names.items()):
        if len(states) > 1:
            out.append(base.Finding("warn", "plans.topic-split-across-states", f"plans/{bucket}",
                                    f"topic `{topic}` exists in both open/ and completed/", repo))
    return out


def run(ctx: base.Context) -> list[base.Finding]:
    plans_dir = ctx.root / "plans"
    if not plans_dir.is_dir():
        return []
    out: list[base.Finding] = []
    allowed = set(ctx.declared_buckets()) | set(ctx.cfg["fixed_buckets"].values())
    # an exempt repo (no bot script) still keeps its plans in the central store under its declared bucket
    allowed |= {ctx.bucket_of(repo) for repo in ctx.repos if ctx.is_exempt(repo) and ctx.bucket_of(repo)}
    for bucket_dir in sorted(p for p in plans_dir.iterdir() if p.is_dir()):
        repo = ctx.repo_for_bucket(bucket_dir.name)
        if ctx.repo_filter and repo != ctx.repo_filter:
            continue
        if bucket_dir.name not in allowed:
            out.append(base.Finding("warn", "plans.bucket-has-repo", ctx.rel(bucket_dir),
                                    f"plans/{bucket_dir.name}/ is not the `Plans bucket` of any repo", repo))
        out.extend(_check_bucket(ctx, bucket_dir))
    code, listing = base.git(ctx.root, "ls-files", "plans")
    if code == 0:
        for path in listing.splitlines():
            if re.match(r"^plans/[^/]+/[^/]+/[^/]+/tmp/", path):
                out.append(base.Finding("error", "plans.tmp-not-tracked", path,
                                        "tmp/ under a plan topic is transient: never track it"))
    return out
