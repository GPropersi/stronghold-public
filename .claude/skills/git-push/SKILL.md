---
name: git-push
description: Review unpushed code on the current branch with 5 parallel subagents, auto-fix mechanical findings via /run-review, ask about design decisions, re-review, then push and create or update the GitHub PR. Use to push, review-and-push, or create a PR. Optional argument is `<branch>` or `<repo-dir>:<branch>`.
---

# Git Push with Multi-Agent Review

Review all code not yet on the remote branch, then push if approved or output findings.

## Config resolution (read the working repo's `## Claude Config`)

This skill is repo-agnostic. It reads every repo-specific value from the working repo's `## Claude Config`
section (schema in `~/code/CLAUDE.md`). Throughout this file, these placeholders resolve to
seam keys:

- `<default-branch>` = the repo's **Default branch** (protected branch; e.g. `main`).
- `<repo>` = the value of the working repo's `Plans bucket` seam key (the directory under `~/code/plans/`).
- `<repo-dir>` = the working repo's directory name (the basename of its primary clone under `~/code`); the value passed to the scripts and used in `~/code/<repo-dir>` paths.
- `<Repo slug>` = the repo's **Repo slug** as `<owner>/<repo>`.
- `<token-generator>` = the repo's **Token generator** script path (used only via the `GH_TOKEN=$(...)` prefix).
- `<bot-push-script>` = the repo's **Bot push script**.

The project board, bot identity and PR reviewer seam keys are consumed by `pr-finish.sh` (Step 9).
Account-specific GraphQL IDs (project-board node, Status-field, "In progress" option, bot node) are **never
inlined** — `pr-finish.sh` resolves them at runtime by name. If a repo omits a seam key, degrade gracefully:
fall back to the ambient value and note the assumption rather than erroring.

## Branch Guard

**Precondition: you MUST be inside the working checkout of the SUB-REPO (`<worktree>`), not the stronghold.** Every command in this skill (`git diff`, `git branch --show-current`, `push-gate.sh`, the bot push) runs against the current working directory. The cwd (`$CLAUDE_PROJECT_DIR`) defaults to `~/code` (the stronghold, remote `OWNER/stronghold`), which sits on `main` *by design*; running git-push from there would review and push the stronghold's workflow files instead of your sub-repo code.

**Argument forms:** none, `<branch>`, or `<repo-dir>:<branch>` (e.g. `app-main:feat/soft-delete-trash-api`, the form `/run-plan` prints). A `<repo-dir>:` prefix names the working repo (the directory under `~/code`) regardless of session context; a `:` never appears in a git branch name, so the split is unambiguous.

Resolve `<worktree>` **once** with `~/code/.claude/scripts/wt-resolve.sh <repo-dir> --branch <branch> [--assert]` (`<branch>` = the branch you were asked to push, else, with no argument, the branch of the sub-repo checkout you are already in via `git branch --show-current`; an empty result means detached HEAD, so STOP and report; the script's header documents the rules and it prints JSON with `kind`, `worktree_abs` and `problems`). Consequences: `kind=none` means the branch is checked out nowhere, so there is nothing to push from a worktree: STOP and report (a new worktree is `make wt-new REPO=<repo-dir> BRANCH=<branch>` from `~/code`, orchestrator only; `has no worktree support` means use the primary clone `~/code/<repo-dir>`); `kind=primary` means `<worktree>` is the primary clone; an `--assert` mismatch (the checkout is not on the branch you were asked to push) means STOP and report. Never check out, switch or invent a branch. Use bare `git ...` instead of `git -C ~/code/<repo-dir> ...` when that path is already the cwd, because a redundant `-C` is blocked by a hook.

**Orchestrator-only cwd rule:** Steps 0, 7d, 8 and 9 (`push-gate.sh`, the bot push script and `pr-finish.sh`) run ONLY at orchestrator level, each after a standalone `cd` into `<worktree>` as its own Bash call (never compound `cd … && …`), never from a subagent: the review subagents, `/run-review` and the design-decision implementations stay subagents and never call them. If the orchestrator cannot set the cwd to the checkout, STOP and report. Every other git command can run as `git -C <worktree> …`, because Bash cwd does not persist between calls in subagents. If `git rev-parse --show-toplevel` resolves to `~/code` (equivalently, `git remote get-url origin` is the stronghold's `OWNER/stronghold`), you are in the stronghold: STOP and target `<worktree>`. (If the user genuinely intends to push the stronghold itself, that goes direct to `main` via `stronghold-sync.sh`, not this skill.)

Then check the current branch (now the sub-repo checkout's). This skill never runs `git checkout` or `git switch` to reach a branch:
1. If on the repo's **Default branch** (or another protected/`master` branch):
   - **Single documented exception** (~/code/.claude/worktree-discipline.md → Worktree Discipline part (3)): this is the one place a branch may be created from the current checkout, because `wt-new` bases on `origin/<default-branch>` and cannot carry already-committed local commits. It is confirm-gated and orchestrator-only (never a subagent).
   - Suggest a branch name based on the task context (e.g., `refactor/splash-validation`, `fix/login-error`)
   - Ask the user: "You're on `<default-branch>` with already-committed local commits. Want me to create and switch to `<suggested-branch>` from this HEAD?"
   - Do NOT proceed until the user confirms and the branch is created. With no already-committed local commits, there is nothing to push: STOP and point to Worktree Discipline (`make wt-new REPO=<repo-dir> BRANCH=<branch>` creates a worktree + branch).
2. If already on a feature branch: proceed normally

## Workflow

### 0. Squash-Merge Staleness Precheck (MANDATORY)

**This check is REQUIRED before any other step. Never skip it.**

Run, alone and at orchestrator level (see the Branch Guard cwd rule), from the sub-repo checkout:

```bash
~/code/.claude/scripts/push-gate.sh --precheck <branch>
```

It fetches, detects commits already squash-merged into the default branch (by `git cherry` plus the merged-PR lookup), maps the changed paths onto the repo's `Push gate` rows, runs NO suites and writes NO result file, and prints one JSON report on stdout. A dirty working tree does NOT stop `--precheck` (only the full gate in Step 7d refuses one), so proceed to Step 1 whenever it exits 0. Read the exit code and the JSON, never judge staleness yourself:

- **Exit 0** (`stale.status` is `CLEAN`): proceed to Step 1.
- **Exit 3 with `STALE`** (or any other non-zero exit): **STOP.** Do not proceed to Step 1 or any other step. Relay the printed reason. For `STALE`:
  1. Inform the user: *"Found N commits on this branch that are already in the default branch (likely squash-merged via a prior PR). This branch must be moved onto a fresh branch from the default branch before pushing, otherwise the PR will contain duplicate/stale changes."*
  2. List the stale commits from the JSON (`stale.commits`).
  3. Use `AskUserQuestion` to offer the fix:
     - question: "This branch has N commits already squash-merged into the default branch. Move the net changes onto a fresh branch? No force-push is needed."
     - header: "Stale branch"
     - options: `[{"label": "Move to a fresh branch (Recommended)", "description": "Create a backup branch, then recreate the net diff on a new branch from the default branch"}, {"label": "Abort push", "description": "Stop here, I'll handle it manually"}]`
  4. If the user selects the fix: create the backup with `git branch backup/<branch-name>` (the one creating form the branch-switching hook allows), then:
     - **In a repo WITH worktree support**: create the fresh branch with `make wt-new REPO=<repo-dir> BRANCH=<new-branch>` (run from `~/code` as its own Bash call, orchestrator only; never raw `git checkout -b` or `git branch`), then, in a standalone `cd` into the new worktree, apply the net diff with `git diff origin/<default-branch> backup/<branch-name> | git apply` (the two-dot form against the fetched default branch, so the already-squash-merged changes are not re-applied), commit it there, and re-run `push-gate.sh --precheck <new-branch>`. NO force-push.
     - **In a repo WITHOUT worktree support** (`make wt-new` reports "has no worktree support"): the agent NEVER creates the branch (the registered hook denies it). STOP, and print these exact manual commands for the user to run in their own terminal: `git checkout <default-branch>`, `git pull --ff-only`, `git checkout -b <new-branch>`, `git diff origin/<default-branch> backup/<branch-name> | git apply`, then commit. Tell the user to re-invoke `/git-push` on the new branch afterward. This is the only no-worktree path.
  5. **Only proceed to Step 1 after a re-run of `push-gate.sh --precheck` exits 0.**

### 1. Gather the Diff

Determine what code exists locally but not on the remote (substitute the repo's **Default branch** for `main` below):

```bash
# Get branch name and remote tracking info
BRANCH=$(git branch --show-current)

# Check if remote branch exists
if git rev-parse --verify origin/$BRANCH >/dev/null 2>&1; then
  # Diff against remote branch
  git diff origin/$BRANCH...HEAD
  git diff origin/$BRANCH...HEAD --stat
  git log origin/$BRANCH..HEAD --oneline
else
  # New branch — diff against the default branch
  git diff origin/main...HEAD
  git diff origin/main...HEAD --stat
  git log origin/main..HEAD --oneline
fi

# Also include uncommitted staged/unstaged changes
git diff HEAD
git status --short
```

If the diff is empty (nothing to push), inform the user and stop.

### 2. Resolve Plan Folder

Pick the plan topic folder in the **central stronghold store** `~/code/plans/<repo>/{open,completed}/<topic>/`, where `<repo>` is the value of the working repo's `Plans bucket` seam key (see the repo's **Plans store (central)** seam) and `<repo-dir>` is the working repo's directory name, where this branch's push review belongs. The app branch/PR does NOT contain its own plan; the plan lives in the central store, so the script searches `open/` then `completed/` there, never the working sub-repo's tree. The scoring is deterministic code, not a model judgment: run

```bash
~/code/.claude/scripts/plan-folder.sh <repo-dir> <branch>
```

from the sub-repo cwd. It prints JSON on stdout (`decision`, `plan_dir`, `candidates`, `suggested_new_folder`); exit 4 means the repo's seam has no `Plans bucket` key (STOP and report). Branch on `decision`:

- **`match`**: Set `<plan-dir>` to the returned `plan_dir`. Skip to "Set tmp directory" below.
- **`ambiguous`** or **`no_match`**: Present `AskUserQuestion` with:
  - One option per entry in `candidates` (label = bare topic folder name, description = `summary` from the entry, falling back to `"(no plan file)"` if `summary` is null).
  - One option labeled `"Create ~/code/plans/<repo>/completed/<suggested_new_folder>/"` with description `"New review-only topic folder for this branch (no plan, so nothing to track as open)"`.
  - The user may also pick "Other" to type a custom folder name.
  - `multiSelect: false`. `header: "Plan folder"`.
  After the user answers: if they chose "Create …" (or typed a custom name that is not an existing topic), run `~/code/.claude/scripts/plan-lifecycle.sh review-topic <repo-dir> <name>` and set `<plan-dir>` to the path it prints (it creates `completed/<name>/{reviews,tmp}` or reuses an existing topic of that name; a plan-less push review never goes under `open/`, where it would sit forever with no plan to finish). Otherwise set `<plan-dir>` to the chosen folder path.

**Set tmp directory:**
- Set `<tmp-dir>` to `<plan-dir>/tmp/` and `mkdir -p` it.

**CRITICAL: A topic's `tmp/` in the central store (e.g. `~/code/plans/<repo>/open/<topic>/tmp/`) must never be used as the final storage location for plans, reviews, or any persistent document.** `<tmp-dir>` (`<plan-dir>/tmp/`) is for transient subagent output only — files are deleted after evaluation. Final documents (push reviews, plans) always go under `<plan-dir>/` in the central store.

Store `<plan-dir>` and `<tmp-dir>` for use in all subagent prompts below.

### 2c. Read Issue Link from Plan Frontmatter

The plan file's YAML frontmatter (written by `/plan-creator` Step 4e) carries the GitHub issue number used to close the issue on PR merge.

1. Locate the canonical plan file: glob `<plan-dir>/*.md`, excluding `*-master.md` and anything under `<plan-dir>/reviews/`. If exactly one match, that's `<plan-file>`. If multiple, pick the one whose name most closely matches `$BRANCH`. If zero matches, skip to Step 2d.
2. Read the first ~20 lines of `<plan-file>`. Capture, unconditionally:
   - **`<plan-branch>`** — from the `branch:` line, if present. If it is set and does **not** equal `$BRANCH` (the branch you are about to push), STOP and surface it via `AskUserQuestion`: the plan expects branch `<plan-branch>` but you are on `$BRANCH` — this usually means a branch drifted mid-run (work landed on a stray/renamed branch). Offer: (a) push `$BRANCH` anyway and update the plan's `branch:` to match, (b) switch to the plan's worktree (`git -C <worktree> …`) and push `<plan-branch>` from there (offer this only when a `worktree:` frontmatter value exists and is verified against `git -C ~/code/<repo> worktree list --porcelain`; use bare `git ...` instead when `~/code/<repo>` is already the cwd — a redundant `-C` is blocked by a hook), or (c) abort. Never check out `<plan-branch>` to resolve the mismatch. Do not silently push the mismatched branch. (Canonical rule: ~/code/.claude/worktree-discipline.md → Worktree Discipline.)

   Issue capture is numeric-only: only a numeric `github_issue:` value is an issue. When it is numeric, capture `<plan-issue-number>` (the integer) and `<plan-issue-url>` (from the `github_issue_url:` line). `github_issue: none` is the explicit waiver: capture nothing, add no `Closes` line, do NOT trigger Step 2d, and print no warning. An empty, `null` or other non-numeric value also captures nothing and surfaces the same final-report warning as a missing key, also without Step 2d.
3. Also glob `<plan-dir>/*-master.md`. If exactly one match and its frontmatter has a numeric `github_issue:` (never `none`), capture `<master-issue-number>`. Otherwise skip the umbrella check.
4. If `<master-issue-number>` was captured, determine whether this is the **final phase**:
   - Read the master plan. For each `**Branch:**` line (excluding the verification phase), find the corresponding sub-plan file within the same topic cluster in the central store (`<plan-dir>`, i.e. `~/code/plans/<repo>/{open,completed}/<topic>/`; master + its sub-plans share one topic folder). Read each sub-plan's `## Status` block.
   - If every phase except the current one has `finished: true`, set `<is-final-phase>=true`. Otherwise `<is-final-phase>=false`.

These values feed into the PR body in Step 9. If 2c found a `<plan-file>` with no `github_issue:` line (or an empty/`null`/non-numeric value), do NOT trigger Step 2d — the plan exists but was created before this convention (or the issue creation failed in `/plan-creator`); surface a warning in the final report instead. `github_issue: none` needs no warning.

### 2d. Auto-Create Issue for No-Plan PRs

Skip this step if 2c found a `<plan-file>`. Trigger only when no plan file exists in `<plan-dir>` (a fresh folder or hotfix branch).

1. Draft a title and body from branch + commits:
   - Title: title-case the branch name with `/` stripped (e.g., `hotfix/login-timeout` → `Hotfix: Login timeout`)
   - Body: condensed bullets from `git log origin/<default-branch>..HEAD --pretty=format:"- %s"`, wrapped in a single `## Why` section
2. Present via `AskUserQuestion` (`header: "No-plan PR"`):
   - **Create as drafted** — proceed with the title and body shown
   - **Edit title** — orchestrator follows up with a free-text prompt for the new title, body stays
   - **Link to existing #** — orchestrator follows up with a free-text prompt for the issue number; validate via `GH_TOKEN=$(<token-generator>) gh issue view <N> --repo <Repo slug>` before accepting
   - **Skip** — no issue, PR will not include `Closes #`. Surface a warning in the final report.
3. On **Create** / **Edit**: call `gh issue create` (with `--repo <Repo slug>`) with the title/body and a single inferred label (resolved from the repo's label set — see the label judgment in Step 9). **Do not add the issue to the project board** — hotfixes are not roadmap items. Skip the bot assignment for the issue (the self-assignee is cosmetic and `pr-finish.sh` handles PRs only).
4. Capture the result as `<plan-issue-number>` and `<plan-issue-url>` (same vars as 2c) so Step 9's body construction stays uniform.

### 3. Launch 5 Parallel Review Subagents

Read `~/code/.claude/skills/git-push/references/subagent-prompts.md` for the full prompt definitions and expected response format.

Launch **all 5 subagents in parallel** using the Agent tool with **`subagent_type: "reviewer"`** (`.claude/agents/reviewer.md`: Read/Grep/Glob/Bash/Write only, so it starts at ~15k tokens instead of ~36k; NEVER use `subagent_type: "Explore"`, which cannot use the Write tool). Each subagent:
- Receives the full diff output from Step 1
- Receives its specific review focus area from the reference file
- Receives `<tmp-dir>` and its assigned output filename(s) (see table below)
- Writes its JSON response to `<tmp-dir>/<role>.md` and returns only: `Written to <path>`
- Uses `model: sonnet` for speed
- Issues independent reads (the diff, source files, `CLAUDE.md`) together in one message as parallel tool calls instead of one per turn

Subagents (all launched in a single message):

| # | Name | Focus | Output file |
|---|---|---|---|
| 1 | Safety & Security | XSS, injection, secrets, OWASP, destructive ops | `<tmp-dir>/safety-security.md` |
| 2 | Correctness | Logic errors, edge cases, type issues, wrong APIs | `<tmp-dir>/correctness.md` |
| 3 | Hygiene (Simplicity, Completeness, Consistency) | Over-engineering and dead code; debug artifacts, TODOs, stubs; project conventions, naming, imports | THREE files, one per checklist: `<tmp-dir>/simplicity.md`, `<tmp-dir>/completeness.md`, `<tmp-dir>/consistency.md` |
| 4 | Test Coverage | Missing tests, untested new behavior, coverage gaps | `<tmp-dir>/test-coverage.md` |
| 5 | Integration Risk | Breaking changes, missing migrations, cross-module impact | `<tmp-dir>/integration-risk.md` |

The Hygiene agent evaluates its three checklists separately and writes a complete schema-valid JSON file for each, so the coordinator (Step 4a) and the review file still list all 7 reviewer roles (`#### 3.` Simplicity, `#### 5.` Completeness, `#### 6.` Consistency); only the agent count changed. *Why:* 4 weeks of push reviews (127 review blocks) showed Simplicity and Completeness as the sole failing reviewer in 1 and 0 reviews, so a separate agent for each paid a full diff read and context start-up for almost no unique findings.

### 4a. Coordinate the Findings (script)

After all 7 reviewer files are written (by the 5 agents), deduplicate and conflict-detect them with code, not a model (`<branch-slug>` = the branch with `/` replaced by `-`):

```bash
python3 ~/code/.claude/scripts/lib/review_tools.py coordinate <tmp-dir> --out /tmp/claude/coordinator-<branch-slug>.json
```

It groups findings by file and line proximity, classifies each group as `unique`, `duplicate` or `conflict` (differing suggestions at one location are ALWAYS a conflict, escalated to `fix_type: "design_decision"`), treats a missing or unusable reviewer file as that reviewer's FAIL, and prints only the output path. The model only Reads that JSON file; it never merges or relabels findings.

### 4b. Evaluate and Classify Results

Read `/tmp/claude/coordinator-<branch-slug>.json` written by Step 4a.

Parse:
- `reviewer_verdicts` — per-reviewer PASS/FAIL map (used for Step 7c re-review targeting)
- `findings` — deduplicated, conflict-annotated finding list

Evaluate:

- **ALL PASS, no findings at all**: Proceed to Step 7d (test gate), Step 7e (size check), then Step 8 (push).
- **ALL PASS, only minor findings**: Write findings (Step 5), classify. If any findings exist (mechanical or design), proceed to Step 6. Otherwise proceed to Step 7d (test gate), Step 7e (size check), then Step 8 (push).
- **ANY FAIL**: Write findings (Step 5), classify, proceed to Step 6.

Partition all findings into two lists:
- `mechanical_fixes[]` — all findings with `fix_type: "mechanical"`
- `design_decisions[]` — all findings with `fix_type: "design_decision"` (includes escalated conflicts)

Track which reviewers returned FAIL from `reviewer_verdicts` (needed for re-review in Step 7c).

Track `fix_round = 1` (used in Step 7c to cap fix-review loops).

Capture `PRE_FIX_SHA = $(git rev-parse HEAD)` — the commit hash before any fix rounds begin. This anchor is captured **once** and reused across all re-review passes in Step 7c to scope the fix diff.

### 5. Write Findings

Push review file lives at `<plan-dir>/reviews/push-review-<branch>.md` in the central store, where `<plan-dir>` (`~/code/plans/<repo>/{open,completed}/<topic>`) was resolved in Step 2. `mkdir -p <plan-dir>/reviews/` if it does not exist. A topic's `tmp/` must never contain final review documents.

#### File and Review Numbering

The numbering and the skeleton come from code: `python3 ~/code/.claude/scripts/lib/review_tools.py section-number <plan-dir>/reviews/push-review-<branch>.md` prints the next `N` (1 for a missing file), and `python3 ~/code/.claude/scripts/lib/review_tools.py scaffold <branch> <N> origin/<default-branch> <verdict>` prints the header (only for review 1) and the section skeleton with the exact headings the downstream skills count (`--verdicts k=PASS,...` fills the reviewer lines). Append that skeleton to `<plan-dir>/reviews/push-review-<branch>.md` (creating the file for review 1) and fill in the findings below it. The shapes it produces are:

The file header (written only on first creation):
```markdown
# Push Review: <branch>
```

Each review section appended to the file:
```markdown
## Review <N>
Generated: <YYYY-MM-DD HH:MM>
Comparison: <base>...HEAD
Verdict: **BLOCKED** or **PUSHED WITH MINOR FINDINGS**

### Results by Reviewer

#### 1. Safety & Security — <PASS/FAIL>
<summary + findings bullet list>

#### 2. Correctness — <PASS/FAIL>
...

#### 3. Simplicity & Conciseness — <PASS/FAIL>
...

#### 4. Test Coverage — <PASS/FAIL>
...

#### 5. Completeness & Cleanup — <PASS/FAIL>
...

#### 6. Consistency & Style — <PASS/FAIL>
...

#### 7. Integration Risk — <PASS/FAIL>
...

### To-Do: Mechanical Fixes

- [ ] **<Imperative action>** — <file(s) to change> — <what to do specifically>
- [ ] ...

### To-Do: Design Decisions

#### DD-1: <Decision title>
**Context:** <Why this needs a decision — what the review found and why it can't be fixed mechanically.>

| # | Option | Trade-off |
|---|---|---|
| 1 | <Option A> | <Pro/con> |
| 2 | <Option B> | <Pro/con> |

**Chosen:** _(pending)_
```

Omit `### To-Do: Mechanical Fixes` if there are no mechanical findings. Omit `### To-Do: Design Decisions` if there are no design decisions. Each DD must have at least 2 options with trade-offs.

#### Verdict Labels

| Verdict | Meaning |
|---|---|
| **PUSHED WITH MINOR FINDINGS** | All PASS, minor findings recorded but no fixes needed |
| **RESOLVED — PUSHED** | Had FAILs, all fixed (mechanical + DDs), re-review passed, pushed |
| **BLOCKED** | FAILs remain after 2 fix rounds, or re-review still fails |

**No review file at all** is the outcome for "ALL PASS, no findings" (per Step 4b) — that case proceeds straight to Steps 7d, 7e and 8 and never reaches Step 5, so no verdict label is written.

#### TO-DO Item Guidelines

Each mechanical fix TO-DO item must be:
- **Self-contained**: Include the file path(s), what to change, and why. The implementer should not need to read the reviewer results above.
- **Imperative**: Start with a verb (Add, Update, Extract, Fix, Remove, Replace).
- **Concrete**: Name the exact file, function, variable, or line to change. Avoid vague items like "fix style issues."
- **One logical change**: Each item should be implementable and verifiable independently.

**Include all findings in the TO-DO list regardless of severity** — minor findings must also be actionable items.

For findings with `classification: "duplicate"` (flagged by multiple reviewers), append a `*(flagged by: Reviewer A, Reviewer B)*` attribution after the item description — this explains why a minor finding appears without inflating its severity.

For findings with `classification: "conflict"` that were escalated to `design_decision`, the DD context must name the disagreeing reviewers and their positions explicitly.

Example:
```markdown
### To-Do: Mechanical Fixes

- [ ] **Fix import ordering in `test_api_route_decorator.py`** — line 13: move `ErrorResponse` import after `ContactResponseSchema` import to maintain alphabetical ordering *(flagged by: Correctness, Consistency)*
- [ ] **Remove dead `None` default from `getattr`** — `tests/unit/schemas/test_schema_descriptions.py` line 20: change `getattr(module, name, None)` to `getattr(module, name)` since `name` comes from `__all__` and is guaranteed to exist

### To-Do: Design Decisions

#### DD-1: Replace `HealthDbResponseSchema` with `StatusMessageResponseSchema`?
**Context:** `HealthDbResponseSchema` duplicates `StatusMessageResponseSchema` — identical status + message fields. But the health endpoint might diverge later.

| # | Option | Trade-off |
|---|---|---|
| 1 | Replace with `StatusMessageResponseSchema` | Eliminates duplication now; easy to split later if needed |
| 2 | Keep separate | Explicit coupling to health domain; minor duplication |

**Chosen:** _(pending)_

#### DD-2: Inline helper vs keep separate — reviewer conflict
**Context:** Simplicity and Test Coverage disagree on `backend/utils/health.py:67`. Simplicity flags the helper as single-use and suggests inlining. Test Coverage flags the same function as lacking test coverage and suggests keeping it separate so it can be unit-tested directly.

| # | Option | Trade-off |
|---|---|---|
| 1 | Inline the helper (Simplicity) | Removes indirection; function no longer independently testable |
| 2 | Keep separate and add unit test (Test Coverage) | Preserves testability; minor extra indirection |

**Chosen:** _(pending)_
```

(The examples above are illustrative — file paths and language reflect one repo; substitute the working repo's own layout.)

After writing the review file, inform the user:
- Which reviewers failed and why (brief)
- Path to the full review file and review number (e.g., "Review 2 appended")
- Count of mechanical fixes and design decisions: `"Found N findings (M mechanical, K design decisions)."`
- Then proceed automatically to Step 6 — do NOT ask the user to run `/run-review` manually.

### 6. Auto-Fix Mechanical Findings via /run-review

If `mechanical_fixes[]` is empty, skip to Step 7.

Inform the user: `"Launching /run-review for M mechanical fixes..."`

Launch `/run-review` as a **subagent** (using the Agent tool) targeting the push review file. The subagent processes all unchecked mechanical fix items through the standard `/run-review` pipeline (each item gets `/next-step-taker` with validation, 3-subagent review, tests, and commit).

Subagent prompt:

```
Run /run-review for "push-review-<branch>".

Process ONLY the items under "### To-Do: Mechanical Fixes" — skip any items
under "### To-Do: Design Decisions".

Important overrides:
- Do NOT pause at the end to ask the user — complete the full workflow and return your final report.
- Follow all CLAUDE.md guidelines, including the repo's conventions for running its
  build/test commands (some stacks require running them unsandboxed for container access —
  follow the working repo's CLAUDE.md). Never disable the sandbox for plain git commands.
```

After the subagent returns:
1. Read the review file to confirm mechanical items are crossed off
2. Report to user: `"Applied N of M mechanical fixes."`
3. If any mechanical items were NOT crossed off, report which ones and why
4. Proceed to Step 7

### 7. Present and Apply Design Decisions

If `design_decisions[]` is empty, proceed to Step 7c (re-review).

#### Step 7a: Present via AskUserQuestion

Present each design decision using the `AskUserQuestion` tool (max 4 questions per call, max 4 options each):

- `question`: `"DD-N: <finding title>?"` — include enough context for the user to decide without reading the review file
- `header`: `"DD-N"` (max 12 chars)
- `options`: Each option from the DD table
  - `label`: Short name (1-5 words)
  - `description`: Trade-off text
- `multiSelect`: `false`

**All design decisions must be resolved before pushing.** Do not include a "skip" option. The user can always select "Other" to provide a custom approach.

If there are more than 4 DDs, batch across multiple `AskUserQuestion` calls (4 at a time).

#### Step 7b: Apply Design Decisions via /run-review

After the user answers all DDs:

1. Update the review file: fill in `**Chosen:** <user's choice>` for each DD
2. Rewrite each DD's chosen option as an actionable to-do item (imperative, self-contained, concrete) and add it as an unchecked `- [ ]` item under that DD's section in the review file
3. Launch `/run-review` as a **subagent** to process the newly-added DD items

Subagent prompt:

```
Run /run-review for "push-review-<branch>".

Process ONLY the unchecked items under "### To-Do: Design Decisions" —
the mechanical fixes are already complete.

For each design decision, the user has chosen a specific approach. The chosen
option is recorded in the **Chosen:** field above each item. Implement exactly
what the user chose.

Important overrides:
- Do NOT pause at the end to ask the user — complete the full workflow and return your final report.
- Follow all CLAUDE.md guidelines, including the repo's conventions for running its
  build/test commands (some stacks require running them unsandboxed for container access —
  follow the working repo's CLAUDE.md). Never disable the sandbox for plain git commands.
```

After the subagent returns:
1. Read the review file to confirm DD items are crossed off
2. Report to user: `"Applied N of M design decisions."`

#### Fix-Round Policy (shared by Steps 7c and 7d)

`fix_round` starts at 1 (set in Step 4b) and is shared across re-review (7c) and test-gate (7d) failures — either can increment it, and both cap at the same total. On a failure in either step, ask the code for the verdict instead of doing the arithmetic: `python3 ~/code/.claude/scripts/lib/review_tools.py next-round <fix_round>` prints `proceed` or `blocked` (`next_round`, with `MAX_FIX_ROUNDS = 2`):
- `proceed`: increment `fix_round`, write the new findings to the review file (append as next `## Review N+1` section), classify into mechanical/design, loop back to Step 6 (mechanical fixes) / Step 7a (design decisions), then retry whichever step failed.
- `blocked`: **BLOCK**. Update review file verdict to `**BLOCKED**`. Inform user with review file path. Do NOT push.

#### Step 7c: Re-Review (verification pass)

After all fixes (mechanical + design decisions) are applied and committed:

**Generate two diffs for re-review subagents:**

1. **Full branch diff** (`origin/<default-branch>...HEAD`) — provides overall context of what the branch does
2. **Fix-only diff** (`<PRE_FIX_SHA>..HEAD`) — shows only the changes made during fix rounds

`PRE_FIX_SHA` was captured once in Step 4b before any fixes began. Because it is never updated, each re-review pass sees the cumulative fix diff (all prior fix rounds), which lets later passes verify that earlier fixes weren't regressed.

Save both diffs to `<tmp-dir>/` (e.g., `re-review-full.diff` and `re-review-fixes.diff`).

**Re-run only the subagents that originally returned FAIL** (not all 5; if any of Simplicity, Completeness or Consistency failed, re-run the one Hygiene agent for just the failed checklists and write only those files). Use the same review focus areas from Step 3, but modify the prompt to include both diffs and the following instruction:

```
You are re-reviewing after fixes were applied. You receive two diffs:

1. Full branch diff (full-diff file): the complete branch vs the default branch — use for context only.
2. Fix-only diff (fix-diff file): changes made since the original review — this is your PRIMARY review target.

Your job:
- Verify that the fixes in the fix-only diff correctly address the issues from the original review.
- Check for any NEW issues introduced by the fix commits themselves.
- Do NOT re-flag issues from the original branch code that are resolved in the fix diff.
  If the full branch diff shows problematic code but the fix diff changes that same code,
  the issue is resolved — do not flag it.

When in doubt, read the actual source file at the flagged location to confirm whether
the issue still exists in the current code.
```

Write results to the same `<tmp-dir>/<role>.md` files.

Evaluate re-review results:
- **All now PASS**: Update review file verdict to `**RESOLVED — PUSHED**`. Proceed to Step 7d (test gate), Step 7e (size check), then Step 8 (push).
- **Still FAIL with new findings**: apply the **Fix-Round Policy** above.

### 7d. Mandatory Push Gate (NEVER SKIP)

**This gate runs on every `/git-push` before Step 8, in BOTH the no-findings path and the post-fix path. It runs at orchestrator level (see the Branch Guard cwd rule) and NEVER accepts a subagent's self-reported test result as evidence.** A subagent's "N passed" is a claim; the gate's result file is the proof, and the push hook refuses the bot push without a passing result for the pushed sha.

Rationale: `/git-push` mutates code (mechanical fixes in Step 6, design-decision implementations in Step 7b), frequently adding new test code. The moment it does, any upstream green run is stale and cannot have covered the new code. The gate re-establishes green over the actual pushed tree.

**Capacity read (orchestrator-level Bash call, made immediately before the push-gate.sh launch):** run `make -C ~/code wt-capacity REPO=<repo-dir>` as its own Bash call (`<repo-dir>` = the working repo's directory name; output redirected to `/tmp/claude/wt-capacity-<repo-dir>.txt`, then Read it and delete it). If `suites_ceiling` is 0, AskUserQuestion before launching the gate (never a sleep loop); if `suites_ceiling` is `n/a` there is no gate, proceed. The read-only review subagents are exempt.

**1. Run the FULL gate** after the last fix commit, from the sub-repo checkout (standalone `cd` first), with `run_in_background`:

```bash
~/code/.claude/scripts/push-gate.sh <branch>
```

The script reads the repo's `Push gate` seam key, maps the changed paths to suites (when in doubt, the whole matched suite runs, never an under-scoped one), runs the suites sequentially and writes `<git-common-dir>/push-gate/<sha>.json` (status `running`, then `pass`, `fail` or `na`). It refuses a dirty tracked tree (commit or `git stash` first; untracked files are ignored). Wait for the completion notification, then Read the result file (`push-gate.sh --status <branch>` prints its path and JSON). Per the amended CLAUDE.md "Background job completions" rule, this gate was launched by this skill under the user's explicit `/git-push` request, so a `pass` or `na` result continues straight to Step 7e and Step 8 WITHOUT asking the user again; the hook still refuses the push unless the result passed for the pushed sha. Any other background completion still waits for the user.

The gate runs through `hostq.sh` (host-wide FIFO queue, detached systemd unit): it may print `QUEUED pos N … behind: …` while other heavy jobs hold the slots, and it survives its launching shell being killed. **Exit 75 means the gate was still queued when the wait limit ran out: it never started and is NOT a failure.** Report "queued behind <labels>", check `hostq.sh status`, and rerun the gate (or ask the user) rather than treating it as a failed suite. A result whose `--status` says `INTERRUPTED` (its hostq job is gone) is likewise not a verdict: rerun `push-gate.sh <branch>`.

**2. Evaluate** by the exit code and the result file:

- **Exit 0** (`pass` or `na`): record `Test gate: PASS` (or `N/A`) with each suite and its counts in the review file, then proceed to Step 7e.
- **Exit 1**, or (no exit code available after a killed run) a result file whose status is `running` or `fail` (red): **do NOT push.** Append the failing tests to the review file as a new `## Review N+1` section, classify into mechanical/design (Step 4b rules), then apply the **Fix-Round Policy** above (Step 7c); on the retry path, re-run this gate rather than Step 7c.
- **Exit 2, 3 or 4, or a missing result file**: this is not a fix round. STOP, relay the printed reason to the user and do not push (the hook would refuse anyway); retry once after the user resolves it (dirty tree, stale branch, unmatched path or missing `Push gate` key, a suite that cannot run, a failed fetch).

### 7e. PR Size Check (after reviews and the gate, right before push)

Runs only after Step 7d passes, so it measures the final diff including every review fix (measuring earlier is premature: fixes change the size). Skip it when a PR for this branch already exists (`GH_TOKEN=$(<token-generator>) gh pr view <branch>`): an open PR is past the split point, and `/split-pr` covers that case.

A reviewer cannot meaningfully review a huge PR (a 12k-line PR once shipped as one unit). Measure what the **PR** will contain, i.e. the diff against the default branch, not against a previously pushed remote branch, excluding lockfiles and generated files (substitute the **Default branch** for `main`):

```bash
git diff origin/main...HEAD --numstat -- . ':(exclude)*.lock' ':(exclude)package-lock.json' ':(exclude)go.sum' ':(exclude)*.snap' ':(exclude)*.min.js' ':(exclude)*.pb.go' ':(exclude)*.generated.*'
```

Sum the added and deleted columns (changed lines). **Rough ceiling: about 1500 changed lines.** The real goal is thematic, modular PRs (one coherent concern each), so file count is not a limit; list the files only to help judge themes. Under the ceiling: continue silently to Step 8. Over it: surface it via `AskUserQuestion` (`header: "PR size"`) showing the line total, file and commit counts, and the distinct themes you can see (largest directories and commit subjects), with these options (first one recommended):

- **Split in a new session (Recommended)** — skip Steps 8, 9 and 9b (no push, no PR for the whole branch), write the handoff file below, run Step 10 (sync) so the review file and the handoff are kept, then STOP and print the exact command from "Handoff file" for the user to run in a fresh session. Do not invoke `/split-pr` yourself: this session's context is already full of review rounds, and a split is a long multi-part run that deserves a clean one.
- **Split now, in this session** — same skips, same handoff file and Step 10 sync (so a crash is resumable), then invoke `/split-pr --pre-pr --handoff <handoff-path>` and let it run.
- **Push as one PR anyway** — continue to Step 8 and note the size and the user's decision in the PR body's Summary.

A split only moves already-reviewed code (its union invariant proves the parts reproduce the reviewed tree exactly), so it never re-runs this skill's reviews; it re-proves the tests instead, with a full gate per part (each prefix of the merge order) plus `push-gate.sh` and CI on every part PR, then ships the parts one at a time, each squash-merged before the next is cut. This step's gate result for HEAD is its entry evidence, so make no commit between Step 7d and the handoff.

**Handoff file.** Both split options write `<plan-dir>/split/split-handoff.md` (`<plan-dir>` = the same folder the review file lives under, including a plan-less push's `review-topic` folder; create `split/` if absent). It is a stronghold-tracked write, so it goes out with Step 10's sync. It is NOT `split-manifest.md` (whose existence makes `/split-pr` resume instead of start). It must let a session with zero context from this one start the split, so write it self-contained:

- `Run:` the literal command, `/split-pr --pre-pr --handoff ~/code/plans/<repo>/<open|completed>/<topic>/split/split-handoff.md` (absolute-from-`~` path; the branch is read from the file, so no branch argument)
- Identity: `repo-dir`, `branch`, `worktree` (absolute path; or "primary clone" in the fallback), `head SHA` (literal 40 characters), `base` (default branch plus the `origin/<default>` SHA the size check diffed against), plan path, issue number if any
- Entry evidence: push-gate result file path, status and suites with counts for exactly that head SHA; the push-review file path, final verdict and round count
- Size: changed-line total, file and commit counts, the themes you saw (largest directories, commit subjects) and any suggested part boundaries, labelled **advisory — the slice planner decides**
- `Do not:` commit anything to the branch, push it, or open a PR for it before the split (any new commit invalidates the gate evidence)

Then print to the user, as its own copyable code block: `cd ~/code` (the fresh session's cwd), and the `Run:` line. Never rely on this conversation's context being available to the split session.

The ceiling is a rough warning, not a gate: a single coherent theme (a migration, a mechanical rename, a large test suite) can legitimately exceed it, and a PR well under it that mixes unrelated themes is also worth flagging. Never split, reorder or rewrite commits yourself in this step; `/split-pr` owns that.

### 8. Push

**CRITICAL: Never use bare `git push`.** The repo remote may use SSH, which attributes the push to the user's personal account. This breaks branch protection rules that block self-approval from the last pusher.

**Always push via the repo's Bot push script** (`<bot-push-script>`; after the adoption this is the central `gh-app-push.sh` in the stronghold's scripts directory for every bot-pushing repo). The script:
- Generates a fresh GitHub App installation token on every invocation.
- Stores the token in a locally-scoped variable (not `GH_TOKEN`), avoiding any chance of a stale env-var PAT shadowing it at URL-expansion time.
- Refuses to push to the default/`master` branch and refuses force-push flags.
- Sets upstream tracking after a successful push.

Run it from the sub-repo checkout (standalone `cd` first), passing the literal branch name:

```bash
<bot-push-script> feat/example-branch
```

(Substitute the real branch name for `feat/example-branch`; if the branch name has characters outside `[A-Za-z0-9._/-]`, STOP and report instead of pushing. Any commit made after Step 7d invalidates the gate result, so re-run Step 7d before pushing.) The hook refuses this command without the passing Step 7d result for the pushed sha. The command must carry the LITERAL branch name: never `$BRANCH`, `HEAD`, a refspec or a `refs/` form, because shell variables do not persist between Bash calls and the hook denies those forms.

**Run it WITHOUT `dangerouslyDisableSandbox`.** The bot push script is declared in `sandbox.excludedCommands`, so it runs unsandboxed automatically — its `.git/config` write (persisting upstream tracking) succeeds without the flag. Do **not** add `dangerouslyDisableSandbox`: the flag forces an un-suppressible permission prompt on every push, while the exclusion achieves the same unsandboxed run silently. (Why the unsandboxed run matters at all: the sandbox blocks `.git/config` writes, so otherwise `git push -u`'s upstream tracking won't stick, breaking `gmas` / `git cleanup`'s reliance on `git fetch -p` to detect merged branches.)

After a successful push, proceed to Step 9 (PR creation).

After a successful push, delete all files in `<tmp-dir>/` (i.e., `<plan-dir>/tmp/`).

### 9. Create or Update PR

After a successful push, create or update a PR targeting the repo's **Default branch**.

Run this step at orchestrator level from the sub-repo cwd (see the Branch Guard cwd rule). The PR itself is created or edited with `gh` below; the label, milestone, project-board, assignee and reviewer plumbing collapses to `~/code/.claude/scripts/pr-finish.sh <PR_NUMBER> --labels ... --milestone ...` (see "Labels, Milestone, Board, Assignee and Reviewer" below), and the model keeps the judgment: which labels, which milestone, the PR title tag and the body.

#### Environment Requirements

All `gh` commands must use the inline `GH_TOKEN=$(...)` prefix — this pattern is exempted from the command substitution hook:

```bash
GH_TOKEN=$(<token-generator>) gh pr ...
```

Run these sandboxed by default (no `dangerouslyDisableSandbox`) — with `sandbox.network.allowMachLookup: ["com.apple.trustd.agent"]` set, `gh`'s TLS handshake to `api.github.com` completes fine inside the sandbox, and matching `permissions.allow` rules auto-approve them via `autoAllowBashIfSandboxed`. Only fall back to `dangerouslyDisableSandbox: true` if a sandboxed attempt actually fails with a TLS/network error — that fallback always triggers a manual approval prompt with no way to pre-allow it, so treat it as a last resort, not the default.

**CRITICAL:** Never resolve the token in a separate call and inline the raw value — that leaks secrets into logs and is blocked by the raw token hook. Always use the `GH_TOKEN=$(...)` prefix form.

#### Check for Existing PR

```bash
GH_TOKEN=$(<token-generator>) gh pr view <branch> 2>&1
```

- **If a PR exists**: use `gh pr edit` to update title/body if the changes warrant it.
- **If no PR exists**: create one with `gh pr create`.

#### PR Title Format

```
[SUBJECT] Title content
```

Where `SUBJECT` is a short tag naming the dominant change area, adapted to the repo's own conventions. Common tags:
- `refactor` — code restructuring without behavior change
- `frontend` — UI/JS/CSS changes
- `backend` — server/API changes
- `database` — migrations, model changes
- `tests` — test-only changes

Use the most dominant category. If changes span multiple areas, pick the primary one.

#### PR Body Format

**First, honor the repo's PR template if one exists.** `gh pr create --body`/`--body-file` does **not** auto-apply `.github/PULL_REQUEST_TEMPLATE.md` — GitHub only injects that template in the web-UI compose flow, so a scripted PR silently bypasses it. Before composing the body, look for a template at these locations, first match wins:

1. `.github/PULL_REQUEST_TEMPLATE.md`
2. `.github/pull_request_template.md`
3. `PULL_REQUEST_TEMPLATE.md` (repo root)
4. `docs/PULL_REQUEST_TEMPLATE.md`
5. any `*.md` under `.github/PULL_REQUEST_TEMPLATE/` (a directory of named templates — pick the one that best fits the change, else the first)

```bash
ls .github/PULL_REQUEST_TEMPLATE.md .github/pull_request_template.md PULL_REQUEST_TEMPLATE.md docs/PULL_REQUEST_TEMPLATE.md 2>/dev/null
```

If a template exists, **use its section structure as the body skeleton**: keep its exact headings and order, replace each placeholder comment with real content, and tick only the checklist items you actually verified (leave genuinely-inapplicable ones unchecked with a note rather than deleting the line). Then append the `Closes` line(s) below it — if the template already carries a `Closes #<issue>` line, fill it in-place instead of duplicating. Richer detail is fine, but it goes *inside* the template's sections, not in a parallel structure that ignores them. Only fall back to the **default structure** below when the repo has **no** template.

In both cases, append the `Closes` lines when applicable (sourced from Step 2c / 2d):

- Always append `Closes #<plan-issue-number>` if it was captured.
- Also append `Closes #<master-issue-number>` if `<master-issue-number>` was captured AND `<is-final-phase>=true`. This closes the umbrella issue when the final phase merges.
- If neither was captured (Step 2d "Skip" path), append nothing and include a note in the final report that the PR has no linked issue.
- **Never put a closing keyword before an issue reference anywhere else in the body.** GitHub closes `#N` for `close`/`closes`/`closed`/`fix`/`fixes`/`fixed`/`resolve`/`resolves`/`resolved` followed by `#N`, even inside a negation ("so it does not close #39" closed an umbrella issue with five phases left). In prose write "umbrella #39" or "Part of #39". The check under "Closing references (script)" below enforces this.

**Default structure (only when the repo has no PR template):**

```bash
GH_TOKEN=$(<token-generator>) gh pr create \
  --base <default-branch> \
  --head "<branch>" \
  --title "[SUBJECT] Title" \
  --body "$(cat <<'EOF'
# Summary

Brief overview of what this PR does.

## Problem

What issue or need motivated this change.

## Solutions

What was done to address the problem. Reference key files/functions changed.

## Verification Steps

- Tests added/modified and their markers
- How to manually verify (if applicable)
- Build verification status

Closes #<plan-issue-number>
Closes #<master-issue-number>
EOF
)"
```

The trailing `Closes #` lines are how GitHub auto-closes the linked issue(s) on merge. Omit either line if the corresponding number wasn't captured — do not leave the literal `<plan-issue-number>` placeholder in the body.

#### Labels, Milestone, Board, Assignee and Reviewer (script)

The label, milestone, project-board, assignee and reviewer plumbing is code. After creating or updating the PR, choose the labels and the milestone (the judgment below), then run, from the sub-repo cwd:

```bash
~/code/.claude/scripts/pr-finish.sh <PR_NUMBER> --labels "label1,label2" --milestone "<milestone title>"
```

The script checks every name against the repo's real label and milestone sets BEFORE changing anything (an unknown name exits 3 and lists the valid names; re-run with valid ones), applies the labels and milestone (skipping any already present), adds the PR to the seam's `GitHub project board` with Status "In progress", assigns the seam's Bot identity (the documented FORBIDDEN response for App installation tokens is a logged skip, not a failure), and requests the seam's `PR reviewer` (skipped on `n/a`). A repo with no board, bot or reviewer skips that part with a note. Exit 4 means it could not run (no seam, no origin, or a `gh` call failed): STOP and report.

Judgment the model keeps:
- **Labels:** base them on **all changes on the branch** (the full `origin/<default-branch>...HEAD` diff, not just the latest push); add **all labels that apply**. Map the changed paths using the repo's **own module layout**, not a hardcoded `backend/`/`frontend/`: server-side code → a backend/server label; JS/HTML/CSS → a frontend label; migrations/model changes → a database label; test files → a testing label; Docker/CI/build config → an infrastructure label; docs → a documentation label; plus intent labels (`bug`/`enhancement`) inferred from the branch, commits and PR context. Only labels that exist in the repo's set are accepted, so list them first with `GH_TOKEN=$(<token-generator>) gh label list --repo <Repo slug>` if unsure.
- **Milestone:** choose **one**, by the nature and motivation of all changes on the branch: a bug fix or broken-behavior correction → a bug/maintenance milestone; new features, enhancements, refactors and architectural improvements → a feature/roadmap milestone. Use commit messages, branch name and PR context. If the repo declares no milestones, omit `--milestone` and note it in the final report.

#### Closing references (script)

After creating or updating the PR, verify GitHub's own parse of the body, from `~/code` or the sub-repo cwd:

```bash
~/code/.claude/scripts/pr-closing-check.sh <repo-dir> <PR_NUMBER> --expect <plan-issue-number>[,<master-issue-number>] --fix
```

`--expect` lists exactly the issues this PR should close: `<plan-issue-number>`, plus `<master-issue-number>` only when `<is-final-phase>=true`, or `none` when no `Closes` line was added. The script compares the PR's `closingIssuesReferences` with that set; for an unexpected issue it rewrites the closing keyword in the body to `reference` and re-checks. Exit 0 = exact match. Exit 1 = still wrong (a `MISSING` line means the `Closes` line was not written: fix the body and rerun; an unfixable `UNEXPECTED` is linked by hand or by a commit message: report it, never merge). Exit 4 = could not check: STOP and report.

#### Link the PR to the plan (script)

Right after the PR is created or updated, record its URL in the plan so the plans dashboard shows it before the PR merges. When Step 2c found a `<plan-file>`, run (any cwd; idempotent, so run it on every push):

```bash
~/code/.claude/scripts/plan-pr-link.sh add <plan-file> <PR_URL>
```

It adds the URL to the plan's `pr_urls:` frontmatter key. Exit 3 (no frontmatter) or 2 (not a GitHub PR URL) is a warning to report, never a reason to stop the push. Skip when the PR has no plan (Step 2d no-plan PRs). Step 10's stronghold sync carries the plan edit.

#### After PR Creation

Output:
- The PR URL
- Branch name and number of commits
- One-line summary from each review subagent

### 9b. UI Verification Screenshots (capture only; orchestrator level)

Run `git diff origin/<default-branch>...HEAD --name-only` and set `<touched-frontend>` true when the branch changed any JS/TS, template (HTML/Jinja) or CSS file, including an admin-only page. Otherwise skip this step.

When true, capture the **implemented, built feature** with Playwright MCP, at **both a desktop viewport (1280px wide) and a mobile viewport (420px wide), always** — never desktop only, even when the feature is not mobile-specific:
- The Step 7d gate's UI suite already brought up the built stack for `<worktree>`; read its URL from the repo's **App URL** key (a worktree's port comes from the repo's stack-info command). If the stack is down, bring it up with the repo's built-UI start command; if it cannot be brought up, say so in the final report rather than skipping silently.
- Reach an authenticated view with the repo's **Test login** key (for app-main, the scoped `login-with-playwright` skill; an admin page needs an admin login).
- Capture each key state of the change (e.g. closed AND open for a modal, toggle or sheet). Use the final built UI, never a design mock.
- Save under `<plan-dir>/screenshots/` (gitignored in the stronghold; never commit images to the sub-repo), named `<state>-desktop.png` / `<state>-mobile.png`. Do NOT send them here: they are sent in Step 11.

### 10. Sync to stronghold

Steps 1–9 wrote the **push review file** (`<plan-dir>/reviews/push-review-<branch>.md`) and a **changelog** entry into the **stronghold** (`~/code`, remote `OWNER/stronghold`) — not the sub-repo. Persist them with the auto-sync script as the final action. **Do NOT improvise a raw `git push` / `git --git-dir=… push` for this, and do NOT pass `dangerouslyDisableSandbox`.**

Because Steps 1–9 run from inside the working sub-repo, first return to the stronghold root **as its own Bash call** (never compound `cd … && …`):

```bash
cd ~/code
```

Then run:

```bash
.claude/scripts/stronghold-sync.sh "<MESSAGE>"
```

- `stronghold-sync.sh` is declared in `sandbox.excludedCommands`, so it runs unsandboxed for the SSH push automatically — **no `dangerouslyDisableSandbox`** needed (the DDS flag forces an un-suppressible permission prompt; the exclusion achieves the same unsandboxed run silently).
- The script path-scopes its own staging and its safety guard refuses unless origin is `OWNER/stronghold` on `main`, so it only ever syncs the stronghold, never the sub-repo — safe to run from the `~/code` cwd even though the sub-repo work happened elsewhere.
- Keep `<MESSAGE>` free of `;` `&&` `||` `$(` and backticks (the compound-command / substitution guard hooks inspect the whole invocation). Use a scoped message, e.g. `plans(<repo>): git-push <branch> push review + PR #<N>`.
- End the final report with one line: `Tip: /clear before the next skill (e.g. /gmas after merge) — the PR, commits and review file hold all state, so a fresh context loses nothing and cuts token spend.`

### 11. Send UI Screenshots (the very last action — only when Step 9b captured any)

After the final report and the `/clear` tip line are printed, send every desktop and mobile screenshot saved in Step 9b with `SendUserFile`, one call per image (desktop then mobile for each state). Nothing else is printed or run after this step.

## Important Notes

Novel rules not stated elsewhere in this file:
- If there are uncommitted changes, warn the user and require them to commit, or `git stash`, the changes before the full gate runs (Step 7d refuses a dirty tracked tree, so the push could never pass the hook; untracked files are ignored by the gate).
- All subagent launches must be in a single message for true parallelism.
- **Orchestrator turn discipline** (this main context routinely reaches 270k+ tokens and every turn re-reads it): keep status lines to one sentence; do not re-narrate findings that already sit in the coordinator JSON or the push-review file; Read the diff and review artifacts once and reuse them; wait for background jobs by completion notification or `wait-for.sh`, never with `echo` or sleep turns; batch independent Bash/Read calls into one message. None of the review, fix, gate or push steps are skipped or shortened.
- If a subagent fails to return valid JSON (or its output file is missing/unreadable), treat it as FAIL with a note about the parse error.

Everything else governing this workflow is fully specified at its point of use — see: reviewer-vs-gate trust boundary (Step 7d), never push to the default/`master` branch / never force-push (Step 8), push review file location (Step 5), DD resolution requirement (Step 7a), the shared `fix_round` cap (Steps 7c/7d, via `next-round`), `/run-review` subagent delegation (Steps 6/7b), and the coordinator's conflict escalation (Step 4a).
