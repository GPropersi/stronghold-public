---
name: next-step-taker
description: Execute the next step in a plan or apply the next item from a review. Use when asked to take the next step, continue a plan, or implement review feedback. The argument is a plan or review name (e.g., "/next-step-taker my-feature"). By default executes plan steps; when the user explicitly asks to implement review changes, applies review items instead.
argument-hint: Plan-or-review-name
---

# Next Step Taker Skill

This skill executes the next incomplete step from a plan OR applies the next pending item from a review file. It validates changes, updates tracking, and pauses for user confirmation before continuing.

## Repo Config (seam)

This skill is repo-agnostic. Read the **working repo's `## Claude Config`** section (schema in `~/code/CLAUDE.md`; filled-in reference example in `~/code/app-main/CLAUDE.md`) for every repo-specific value, and refer to those keys below rather than hardcoded commands, paths, or markers:

- **Default branch** — the protected branch the Branch Guard checks against.
- **Container runtime** — how to run containerized/build commands and any shell setup they need (e.g. activating a virtualenv).
- **Commands** table — the **Integration tests**, **UI tests**, **JS/unit tests**, and **Build** command rows used for validation.
- **Plans store (central)** — the central stronghold plans store. All plans/reviews now live in `~/code/plans/<repo>/`, where `<repo>` is the value of the working repo's `Plans bucket` seam key (a missing key means STOP and report it; plans about `~/code` itself use the literal bucket `stronghold`) and `<repo-dir>` is the working repo's directory name (the basename of its primary clone under `~/code`), which is what is passed to `wt-resolve.sh` and used in `~/code/<repo-dir>` paths. Read both from the working repo's `CLAUDE.md` at runtime — never hardcode a repo name. Layout: active topics live under `~/code/plans/<repo>/open/<topic>/`; finished topics under `~/code/plans/<repo>/completed/<topic>/`; per-plan `reviews/`, `research/`, and `mocks/` are subfolders of the plan's `<topic>/` dir; transient scratch goes in `~/code/plans/<repo>/open/<topic>/tmp/` (deleted after use).

If a key is absent (seam not fully rolled out for that repo), degrade gracefully: fall back to the ambient value and note the assumption rather than erroring.

## Branch Guard

**First, resolve `<plan-branch>` and `<worktree>` once** per `~/code/CLAUDE.md` → Worktree Discipline, part (1). Locate the target plan file from **$ARGUMENTS** (cheap glob by name) and run `~/code/.claude/scripts/wt-resolve.sh <repo-dir> --plan <plan-file> [--assert]`; it reads the plan's `branch:` and `worktree:` frontmatter, verifies them against `git worktree list --porcelain` (the authoritative branch-to-path map), and prints JSON (`kind`, `worktree_abs`, `worktree_rel`, `problems`; the script header documents the rules). This skill never runs `git checkout` or `git switch`.

- **If `<plan-branch>` is recorded:** `<worktree>` is the script's `worktree_abs` (`kind=linked` is a verified worktree, backfill `worktree:` in the plan from `worktree_rel` when missing; `kind=primary` is the primary clone `~/code/<repo-dir>`; stronghold and Default-branch plans never get a worktree: `<worktree>` is the primary, or `~/code` for `repo: stronghold`). If `kind=none` (the branch is checked out nowhere), run `make wt-new REPO=<repo-dir> BRANCH=<plan-branch>` from `~/code` (own Bash call, orchestrator only), then re-run the script; `has no worktree support` means fall back silently to the primary clone, any other failure means STOP and report. Then assert: `git -C <worktree> branch --show-current` (use bare `git ...` instead when `<worktree>` is already the cwd (e.g. `~/code` for stronghold plans) — a redundant `-C` is blocked by a hook) must equal `<plan-branch>`. An `--assert` mismatch (or, by hand, a branch mismatch; or if `<plan-branch>` does not exist locally, e.g. squash-merged/deleted or a stale record), STOP and report back; never check out, switch, or invent a branch. Then proceed.
- **If NO `branch:` is recorded** (older plan), `<worktree>` is the primary clone `~/code/<repo-dir>`:
  1. If its current branch (`git -C ~/code/<repo-dir> branch --show-current`, bare `git` when that path is the cwd) is the repo's **Default branch** (or another protected trunk such as `main`/`master`): there is no feature branch yet. Follow `/plan-creator`'s Branch Guard (fetch `origin/<default>`, confirm via `AskUserQuestion`, `make wt-new REPO=<repo-dir> BRANCH=<suggested-branch>`, the `has no worktree support` fallback), suggesting a branch name from the task context (e.g., `refactor/splash-validation`, `fix/login-error`). Do NOT proceed until the user confirms; then **write `branch:` (and `worktree:` when one was created) into the plan's frontmatter** so subsequent runs/subagents reconcile onto it.
  2. If it is a feature branch: proceed normally, and **backfill `branch: <current-branch>` into the plan's frontmatter** so the record exists for next time.
- **Push reviews (Review mode, no plan file):** `<plan-branch>` and `<worktree>` come from Review Mode Step 1 (the push-review header plus `wt-resolve.sh`), not from plan frontmatter.

**The `WORKTREE DISCIPLINE (critical):` block** (substitute `<plan-branch>` and `<worktree>`, expanding `~` to the full absolute home path since Edit/Write do not expand it; in the primary-clone fallback `<worktree>` is `~/code/<repo-dir>`). Prepend it verbatim to every sub-subagent prompt that reads, edits, builds, tests or commits sub-repo code, including read-only review subagents:

> **WORKTREE DISCIPLINE (critical):** The working branch for this plan is `<plan-branch>`, checked out at `<worktree>`. Before reading or editing any file, running a build/test, or committing, run `git -C <worktree> branch --show-current` (bare `git branch --show-current` when `<worktree>` is already the cwd, e.g. `~/code` for stronghold plans — a redundant `-C` is blocked by a hook). Bash cwd does not persist between calls: use `git -C <worktree>` for every git command and absolute paths under `<worktree>` for everything else (builds/tests: `make -C <worktree> <target>` or `--project-directory <worktree>`, never a prior `cd`). If it is **not** `<plan-branch>`, **STOP and report back** to the orchestrator. **NEVER** run `git checkout`, `git switch`, or any command that creates a branch or worktree (`git checkout -b`, `git branch <name>`, `git worktree add`) to fix it. Edit sub-repo files only by absolute path under `<worktree>`; plans, reviews and the changelog stay under `~/code/plans` and `~/code/changelog`. Whatever the reason the branch is wrong (including `<plan-branch>` being checked out in another worktree), STOP and report back; never retry, force, or create a new branch.

When invoked as a subagent by `/run-plan`, the orchestrator already injects this block (with `<worktree>` resolved) into this prompt: assert the branch with `git -C <worktree> branch --show-current`, and STOP and report back on a mismatch (never check out or create anything), and skip re-resolving. Canonical rule: CLAUDE.md → Worktree Discipline.

## Mode Selection

Determine the mode based on user intent:

- **Plan mode** (default): Execute the next incomplete phase/step from a plan document. Use this unless the user explicitly asks to implement review feedback.
- **Review mode**: Apply the next unchecked item from a review file. Use when the user says things like "implement the review changes", "apply the review", "work through the review feedback", or "next review item".

---

## Plan Mode Workflow

### Step 1: Locate the Plan
- Resolve the plan from the **Plans store (central)**, keyed by the working repo's `<repo>` (the `Plans bucket` seam key). Search `~/code/plans/<repo>/open/<topic>/<name>.md` FIRST, then `~/code/plans/<repo>/completed/<topic>/<name>.md`, matching **$ARGUMENTS** contextually (e.g., glob `~/code/plans/<repo>/{open,completed}/**/<name>.md`). Derive the `<topic>` from the plan file's parent directory name.
- Get the outline and the next incomplete step in one call: `~/code/.claude/scripts/plan-status.sh <repo>/<plan-name>` (frontmatter, per-step line ranges and checkbox progress, the non-step sections). Then read the shared context plus ONLY the step you will execute: `plan-status.sh <repo>/<plan-name> --step next --with-context` (or `--step N`). That output holds everything before the first step (Summary, Research Findings, shared reference sections) and your step; read another step (`--step M`) only when yours refers to it. Because `Edit` requires a prior `Read` of the file, use the `Read` tool with `offset`/`limit` on your step's line range before you tick checkboxes in Step 4, not a full-plan read. Fall back to reading the whole plan when it has no `### N.` step headings.

### Step 2: Execute the Step
- **Context discipline** (every turn re-reads your whole context, and executors past ~150k are the costly ones): send command output you expect to exceed ~50 lines to a `/tmp/claude/...` file and read it with `Read` limit or `grep -m`/`-l`; prefer `Read` with offset/limit or `grep -n` over `cat`/full-file reads for files you only need part of; batch independent reads into one message. None of this changes what you must read to implement the step correctly.
- Read all files mentioned in the current step before making changes
- Implement all changes specified in the step following existing code patterns
- Make edits, create files, or delete files as specified in the plan
- Edit sub-repo files only by absolute path under `<worktree>`; translate any plan-step reference written as `~/code/<repo>/...` accordingly.
- Follow the working repo's CLAUDE.md guidelines (coding conventions, no debug leftovers, typing/naming rules, module-communication rules, etc.)

### Step 3: Validate the Changes
**CRITICAL: Always validate after execution.** Choose appropriate validation method(s), keyed to the repo's **Commands** table.

**CRITICAL: Run every test suite, lint, typecheck and build through `~/code/.claude/scripts/run-suite.sh [--expect-branch <plan-branch>] <worktree> <make-target> [VAR=value ...]`** (for example `run-suite.sh --expect-branch <plan-branch> <worktree> test-marker-parallel m=trash`). It runs the repo's make target unsandboxed, keeps the full output in a file, and prints only a compact result (PASS/FAIL, summary counts, up to 20 failing test ids, tail), so no test-runner subagent and no tail/grep/rm follow-up calls are needed. Never run raw `make test-*` or pytest commands that dump their output into your context. Read the kept output file (its path is printed on failure) only for the failures it names. Only a target outside `test-*`/`lint*`/`typecheck`/`vite-build*`/`audit*`, or a stack without `make`, still goes through a subagent.

**Capacity gate (before any validation subagent that starts a test suite):** read capacity with `make -C ~/code wt-capacity REPO=<repo-dir>` as its own Bash call (output redirected to `/tmp/claude/wt-capacity-<repo-dir>.txt`, then Read it and delete it), once per Step 3 (reruns inherit that read). If `suites_ceiling` is 0: as a subagent of run-plan, STOP and report the `suites_ceiling=0` blocker to the orchestrator; in interactive mode, AskUserQuestion before starting the suite. Otherwise proceed: if `suites_ceiling` is `n/a` there is no gate, proceed.

#### For Frontend / Build-Affecting Changes:
Run the repo's **Build** command (from its `## Claude Config` → Commands → Build) through `run-suite.sh` and report pass/fail; on failure Read the kept output file:
- Verify: No import errors, no missing exports, clean build output

#### For Server / Template Changes:
- Verify the app's container starts without errors (per the repo's **Container runtime**)
- Check application logs for issues

#### For UI Changes (browser / end-to-end tests):
- If the plan specifies manual verification steps, list them for the user
- If specific UI tests are mentioned, run them with `run-suite.sh`:
  - The repo's **UI tests** make target (from its Commands table) scoped to the relevant marker/tag `MARKER_NAME`; report the pass/fail counts and, on failure, each failing test name and error.
  - For a specific test file: the repo's single-file test target (for example `test-file f=<path>`).
- **Use the repo's UI tests command as declared in the Commands table** — for some stacks this deliberately runs against pre-built assets rather than a dev server, so prefer whatever "built" UI target the repo specifies.
- Scope UI runs to the repo's own UI test markers/tags (as declared in its test config / Commands conventions) — do not assume any particular marker set.

#### For Unit / Integration Tests:
Run with `run-suite.sh`:
- The repo's **Integration tests** make target (from its Commands table) scoped to the relevant marker/tag `MARKER`; report the pass/fail counts and, on failure, each failing test name and error.
- For a specific test file: the repo's single-file test target (for example `test-file f=<path>`).

#### Subagent Guidelines (for validation work that still needs a subagent, e.g. a stack without `make` targets):
- Use the Agent tool with a clear, self-contained prompt
- Prepend the `WORKTREE DISCIPLINE (critical):` block (defined in Branch Guard; substitute `<plan-branch>` and `<worktree>`) to every validation subagent prompt, so builds and tests run in `<worktree>`, never in a different checkout
- CRITICAL: The subagent must set `dangerouslyDisableSandbox: true` on every Bash call that runs a **Container runtime** command (docker/container/build/`make`-style targets — the Docker socket requires it). Include an example in the subagent prompt: `Bash(command: "<repo Integration-tests command scoped to a marker> > \"/tmp/claude/test-results.txt\" 2>&1", dangerouslyDisableSandbox: true)`
- The subagent must apply any container-shell setup the repo's **Container runtime** requires (e.g. activating a virtualenv) before invoking test/build commands
- The subagent should use the repo's declared **Commands** targets when available (preferred over raw container commands)
- Never run two test suites simultaneously (they typically share a single test DB)
- On success: subagent returns pass count only
- On failure: subagent returns failed test names + error/assertion snippets + writes failures to `tmp/` using the **test-failure-logger** pattern

### Step 4: Update the Plan Document
**CRITICAL: Always update progress tracking.**

#### If Progress Tracking Section Exists:
- Change `- [ ] **Phase X: Name**` to `- [x] **Phase X: Name** - COMPLETE (YYYY-MM-DD)`
- Add checkmarks for completed sub-items: `- ✅ Item description`
- Example:
```markdown
- [x] **Phase 4: Navigation Button Migration** - COMPLETE (2026-02-14)
  - ✅ Updated back_home_btn.html - added data-route attribute
  - ✅ Updated logout_btn.html - added data-route attribute
  - ✅ Updated navbar module - added handlers
  - ✅ Build verified - clean build
```

#### If No Progress Tracking Section Exists:
- Create a new "## Progress Tracking" section at the end of the document
- Add checkboxes for each phase/step mentioned in the plan
- Mark completed items with `[x]` and date

#### Update the Finished Flag:
- If all phases are complete, change `**finished**: false` to `**finished**: true`

#### Move the Finished Topic (lifecycle — topic-as-unit):
**CRITICAL: This skill owns the open → completed lifecycle move.** When the whole topic becomes fully done — the master plan AND all its sub-plans have `finished: true` — after flipping the frontmatter, run the script (never a hand-typed `git mv`):
```
~/code/.claude/scripts/plan-lifecycle.sh complete <repo-dir> <topic>
```
It verifies no plan in the topic still says `finished: false` (exit 3 = not finishable: STOP and report), `git mv`s the WHOLE topic folder from open to completed (its `reviews/`, `research/`, `mocks/` subfolders travel with it), and closes the topic's GitHub issues when the repo has no PRs (the stronghold). In a PR-based repo the PR's `Closes #N` closes them on merge, so the script only prints a note; run `plan-lifecycle.sh close-issues <repo-dir> <topic>` after the merge if `plan-lifecycle.sh audit` later reports `ISSUE-OPEN`. Folder location is the primary open/done signal. Exit 4 after the move means issue closing failed: report it and rerun `close-issues`.

### Step 5: Subagent Review & Fix
**CRITICAL: Always perform this review before reporting to the user.**

Use the **Subagent Review Pipeline** (defined below) to review and fix the implementation. Before launching:
1. Run `git -C <worktree> status --short -uall` (bare `git` when `<worktree>` is the cwd, per the Branch Guard) to collect the list of changed files; unlike a plain name-only diff, it also lists every new untracked file a step created (`-uall` expands new directories to files). Entries are `XY path` relative to `<worktree>`: strip the status prefix, and for a rename use the new path
2. Prepare a brief description of what the step was supposed to implement (from the plan)
3. Launch both review subagents in parallel, then the fix subagent if needed

### Step 6: Report and Pause
- Provide a concise summary of what was completed
- Show validation results (build output, test results, etc.)
- Confirm the plan document was updated
- Include a brief summary of subagent review findings and any fixes applied by the fix subagent
- **REQUIRED:** Ask the user if they want to continue to the next step
- Do NOT automatically proceed to the next step

## Example Output

```
✅ Phase 4: Navigation Button Migration - COMPLETE

Changes made:
- Updated back_home_btn.html (added data-route="home")
- Updated logout_btn.html (added data-route="logout")
- Updated navbar module (added config import and handlers)

Validation:
✅ Build passed - no errors

Subagent Review:
- Correctness, Codebase Fit & Quality: 1 FINDING — stale debug log in navbar module
- Security & Edge Cases: PASS — no injection vectors, missing route falls back safely

Fix Subagent:
✅ Removed debug log from navbar module
✅ Re-validated: Build clean

Plan updated:
✅ Marked Phase 4 as complete in plan document

Ready for Phase 5 (Contact Form Migration)?
```

---

## Review Mode Workflow

### Step 1: Locate and Read Files
- Plan: resolve from the **Plans store (central)** keyed by `<repo>` — glob `~/code/plans/<repo>/{open,completed}/**/<name>.md` where `<name>` matches **$ARGUMENTS** contextually, searching `open/` first then `completed/`. Derive `<topic>` from the plan file's parent directory name.
- Review: reviews live BESIDE their plan in the central store — resolve to `~/code/plans/<repo>/open/<topic>/reviews/<name>-review.md` (or `.../completed/<topic>/reviews/...` if the topic is finished) for plan reviews, or `~/code/plans/<repo>/open/<topic>/reviews/push-review-<branch>.md` for push reviews.

Derive `<topic>` from the plan file's parent directory, or, for push reviews, from the `<topic>` directory that contains the located push-review file's `reviews/` folder.

**For push reviews**: the primary clone's current branch cannot identify the reviewed branch (git forbids a branch checked out in a worktree from also being the primary's current branch), so read it from the review file itself:
- Locate the push-review file from **$ARGUMENTS** (its exact filename, e.g. `push-review-<slug>.md`) by globbing `~/code/plans/<repo>/{open,completed}/*/reviews/<filename>.md`; if there is no match or several, ask the user rather than fuzzy-matching.
- Read `<branch>` from the file's own header line (`# Push Review: <branch>`, written verbatim by `/git-push` Step 5): the exact branch, `/` and all, never parsed from the filename slug. If the header is missing or empty, STOP and ask the user.
- Resolve `<worktree>` from `<branch>` with `~/code/.claude/scripts/wt-resolve.sh <repo-dir> --branch <branch>` (it matches the porcelain `branch refs/heads/<branch>` line exactly, never by prefix; `<repo-dir>` is the sub-repo directory, with `stronghold` standing in for it when the review belongs to the stronghold). If it reports `kind=primary` or `kind=none`, fall back to the primary clone `~/code/<repo-dir>` and **assert** the primary is actually on it: `git -C ~/code/<repo> branch --show-current` (substitute `<repo-dir>` for `<repo>`; bare `git` when that path is the cwd) must equal `<branch>`; on a mismatch, STOP and report back rather than checking out. Use this `<worktree>` and `<plan-branch>` = `<branch>` for the rest of the run.

When **$ARGUMENTS** matches a push review file (e.g., "push-review-refactor-splash"), there is no associated plan file — the review is standalone. Skip reading the plan.

Read the review file in full — review files accumulate multiple revision passes and the latest revision is at the bottom. Paginate with `offset` + `limit` if needed. For plan reviews, also read the plan file in full.

### Step 2: Find the Latest Revision's Pending Items

Review files contain multiple revision sections. **Only the latest revision is authoritative.**

For push review files (`push-review-*`), revisions are numbered: `## Review 1`, `## Review 2`, etc.
For plan review files (`*-review`), revisions use dates or pass labels: `## Review — 2026-03-15 (fifth pass)`.

1. Grep for all `## Review` headings to get line numbers and text
2. Determine the latest: by **number** for push reviews (highest N in `## Review N`), or by **pass label/date** for plan reviews
3. Read from that heading to the next `## Review` heading (or end of file)
4. Find every unchecked item (`- [ ]`) in the To-Do section(s) of the latest revision only. The heading depends on the review type: plan reviews use **"To-Do: Required Changes"**; push reviews (written by `/git-push`) use **"To-Do: Mechanical Fixes"** and **"To-Do: Design Decisions"** (accept either; take items in file order, unless the invoking prompt scopes the run to one of those sections, as `/git-push` does)

If all items are already checked (`- [x]`), report that the review is fully applied and stop.

### Step 3: Apply the Next Unchecked Item

Take the **first** unchecked item only (one at a time):

#### 3a. Apply the Change
- Read all files referenced by the review item before making changes
- If the change is ambiguous or requires a decision the review doesn't resolve, **ask the user before editing**
- Implement the minimal, faithful change to the **codebase** (not just the plan) that fulfills the review item
- Edit sub-repo files only by absolute path under `<worktree>`; translate any plan-step reference written as `~/code/<repo>/...` accordingly.
- Follow the working repo's CLAUDE.md guidelines

#### 3b. Validate the Changes
Use the same validation approach as Plan Mode Step 3 (build verification, tests via subagent, etc.), keyed to the repo's **Commands** table.

#### 3c. Subagent Review & Fix
Use the **Subagent Review Pipeline** (defined below) to review and fix the applied change. Before launching:
1. Run `git -C <worktree> status --short -uall` (bare `git` when `<worktree>` is the cwd, per the Branch Guard) to collect the list of changed files, including any new untracked files (`XY path` entries: strip the status prefix, use the new path for a rename)
2. Prepare a brief description of what the review item asked for
3. Launch both review subagents in parallel, then the fix subagent if needed

#### 3d. Cross Off the Review Item
Mark complete in the review file: `- [ ]` → `- [x]`

### Step 4: Report and Pause
- Summarize what was applied
- Show validation results
- Include subagent review findings and any fixes applied by the fix subagent
- **REQUIRED:** Ask the user if they want to continue to the next review item
- Do NOT automatically proceed

---

## Subagent Review Pipeline

This pipeline is used by both Plan Mode (Step 5) and Review Mode (Step 3c). It replaces inline reviews with parallel subagents to keep the main context window clean.

### Prerequisites

Before launching the review subagents, the main agent must:
1. Run `git -C <worktree> status --short -uall` (bare `git` when `<worktree>` is the cwd, per the Branch Guard) to collect the list of changed files, including any new untracked files (`XY path` entries: strip the status prefix, use the new path for a rename)
2. Prepare a one-line summary of **what was supposed to be implemented** (the intent)

### Phase 1: Two Parallel Review Subagents (Read-Only)

Launch both subagents simultaneously in one message using the Agent tool with `model: "sonnet"`. Each receives:
- The list of changed files
- The implementation intent summary
- Their specific review focus (below)
- Instruction: **read-only — do NOT edit any files**
- Instruction: read only the changed files and their immediate context (imports, callers)
- Instruction: issue independent reads (Read/grep/`git diff`) together in one message as parallel tool calls instead of one per turn; every turn re-reads the whole context

Each subagent returns a structured response:

```
VERDICT: PASS | FINDINGS
Items: (only if FINDINGS)
- severity: critical | major | minor
  file: <path>
  line: <number or range>
  issue: <one-line description>
  suggestion: <one-line fix suggestion>
```

#### Subagent 1: Correctness, Codebase Fit & Quality
- Prompt: prepend the `WORKTREE DISCIPLINE (critical):` block from Branch Guard (read-only subagents get it too; they read source from `<worktree>`)
- Does the implementation follow existing patterns, naming conventions, and architecture?
- Are there logic errors, incorrect assumptions, or fragile implementations?
- Does the code faithfully implement what the plan step / review item specified?
- Are imports ordered per the repo's import-ordering convention?
- Over-engineering or premature abstractions?
- Test coverage: were tests added for new behavior? Happy + sad paths?
- CLAUDE.md compliance: the working repo's coding conventions (typing/annotations, descriptive naming, no single-letter variables, etc.)?
- Clean up: no unused imports, no dead code introduced?

#### Subagent 2: Security & Edge Cases
- Prompt: prepend the `WORKTREE DISCIPLINE (critical):` block from Branch Guard (read-only subagents get it too; they read source from `<worktree>`)
- OWASP concerns: XSS, injection, CSRF vulnerabilities?
- Failure modes: empty input, missing data, unexpected states?
- Leftover debug artifacts: debug logging, global leaks, commented-out debug hacks?
- Sensitive data exposure or insecure defaults?

### Phase 2: Aggregation

The main agent collects both subagent responses:
- If both return `PASS`: skip Phase 3, proceed to reporting
- If any return `FINDINGS`: aggregate all findings into a single list and proceed to Phase 3

### Phase 3: Fix Subagent

Launch a single fix subagent via the Agent tool. It receives:
- The `WORKTREE DISCIPLINE (critical):` block from Branch Guard (substituting `<plan-branch>` and `<worktree>`), prepended to its prompt
- The aggregated findings list from Phase 2
- The list of changed files
- The implementation intent summary
- Instruction: fix all reported findings, then re-validate

The fix subagent must:
1. Read the relevant files
2. Apply fixes for each finding
3. Re-validate using the appropriate method (CRITICAL: every Bash call running a **Container runtime** command — docker/container/build/`make`-style target — MUST set `dangerouslyDisableSandbox: true`):
   - **Frontend / build-affecting changes**: Run the repo's **Build** command — example: `Bash(command: "<repo Build command> > \"/tmp/claude/build.txt\" 2>&1", dangerouslyDisableSandbox: true)`
   - **Backend/code changes**: Run the relevant test marker via the repo's **Integration tests** command scoped to that marker — example: `Bash(command: "<repo Integration-tests command scoped to a marker> > \"/tmp/claude/test-results.txt\" 2>&1", dangerouslyDisableSandbox: true)`
   - **Template/server changes**: Verify the app's container starts without errors (per the repo's **Container runtime**)
4. Return a structured response:

```
FIXES APPLIED:
- file: <path>, line: <number>, change: <one-line description>
- ...

VALIDATION: PASS | FAIL
(if FAIL, include error output)

UNRESOLVED:
- (any findings that could not be fixed mechanically — require user decision)
```

If the fix subagent reports `VALIDATION: FAIL` or has `UNRESOLVED` items, the main agent must surface these to the user in the report step and ask for guidance before proceeding.

### Subagent Guidelines (applies to all 3 subagents)
- CRITICAL: Set `dangerouslyDisableSandbox: true` on every Bash call that runs a **Container runtime** command (docker/container/build/`make`-style target). Example: `Bash(command: "<repo Build command> > \"/tmp/claude/build.txt\" 2>&1", dangerouslyDisableSandbox: true)`
- Apply any container-shell setup the repo's **Container runtime** requires (e.g. activating a virtualenv) before running commands inside the container
- Use the repo's declared **Commands** targets when available (preferred over raw container commands)
- Never run two test suites simultaneously (they typically share a single test DB)
- Keep responses concise — structured format only, no prose

---

## Important Notes

- **Always validate**: Build verification is mandatory for frontend/build-affecting changes
- **Always review via subagents**: The Subagent Review Pipeline is mandatory before reporting to the user
- **Always update tracking**: Mark progress after every step/item completion
- **Always pause**: Never auto-continue to next step without user confirmation
- **CRITICAL: Set `dangerouslyDisableSandbox: true`** on every Bash call running a **Container runtime** command (docker/container/build/`make`-style target) — but **never** on `grep` (read-only; runs bare and sandboxed, allowlisted via `Bash(grep:*)`; DDS on grep forces an un-suppressible prompt)
- **Test runs use synchronous Bash** — every test-command invocation (integration, UI, single-marker, parallel, full suite, per the repo's Commands table) runs via the synchronous `Bash` tool with `dangerouslyDisableSandbox: true`. Never wrap test runs in a `Monitor`, never use `run_in_background`. Output goes to `/tmp/claude/<name>.txt`; this subagent blocks until the command exits and reports the result in its Agent-tool reply.
- **Follow existing patterns**: Read code before making changes
- **Clean up**: Remove debug artifacts (debug logging, global leaks, debug hacks) per the repo's CLAUDE.md
- **Investigate every test failure** — never dismiss a failure as "pre-existing" or "flaky" because the test file wasn't modified on this branch. Current changes can break tests indirectly (shared fixtures, CSS/selector changes, templates, timing, imports). For each failure: read the traceback, check if branch changes could affect the failing path, and either fix it or confirm it's unrelated by rerunning in isolation 2-3 times. Include this rule in all subagent prompts that run or evaluate tests.

## Sync to Stronghold (final step — always run)

When this single step (or review item) and its plan/review tracking update are complete — after Step 4/Step 6 reporting, regardless of whether the run pauses for the user — commit + push the stronghold plans-store / changelog changes to `main` via the central sync script (see `~/code/CLAUDE.md` → "Stronghold Auto-Sync (`.claude/scripts/stronghold-sync.sh`)"):

- Run `.claude/scripts/stronghold-sync.sh "<MESSAGE>"` — it is declared in `sandbox.excludedCommands`, so it runs unsandboxed for the SSH push automatically. Do **not** pass `dangerouslyDisableSandbox` (the DDS flag forces an un-suppressible permission prompt; the exclusion achieves the same unsandboxed run silently).
- `<MESSAGE>` = `plans(<repo>): next-step-taker <plan-name>` — fill in the resolved `<repo>` (the `Plans bucket` value) and the `<plan-name>` (or review name) worked this run.
- The script operates on the stronghold (`~/code`) regardless of your current working directory, stages only `plans/ changelog/ .claude/ CLAUDE.md` etc. (never a blanket add), and **skips cleanly if nothing changed** — so it is always safe to call even if this run wrote nothing to the stronghold. (This is why `/run-plan` and `/run-review` can safely call the same sync again at the end of their loop — a redundant no-op.)
- Do NOT open a PR or feature branch for this — the stronghold pushes direct to `main`. (This syncs only the stronghold; any sub-repo code changes keep their own normal git-commit/git-push/PR flow.)
