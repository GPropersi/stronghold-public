---
name: run-plan
description: Execute all remaining steps of a plan autonomously - each step runs /next-step-taker in a Sonnet subagent while the orchestrator commits and loops, stopping only on blockers or when finished, then prints the /git-push command. Use for "run the plan", "execute all steps", "keep going until done". Argument is the plan name or <repo>/<plan-name>.
argument-hint: plan-name
---

# Run Plan Skill

Autonomously execute all remaining steps of a plan. The **main agent is an orchestrator only** — it reads the plan, delegates each step to a subagent running `/next-step-taker`, commits, and loops. It never directly edits implementation files, runs tests, or runs build/container commands.

## Repo Config (seam)

This skill is repo-agnostic. Read the **working repo's `## Claude Config`** section (schema in `~/code/CLAUDE.md`; filled-in reference example in `~/code/app-main/CLAUDE.md`) for every repo-specific value, and refer to those keys below rather than hardcoded commands, paths, or ports:

- **Default branch** — the protected trunk the Branch Guard and diff-scope checks use.
- **Container runtime** — how to run containerized/build commands and any shell setup they need.
- **Commands** table — the **Integration tests**, **UI tests**, **JS/unit tests**, and **Build** rows used for validation and the final suite.
- **App URL** — the running-app URL used for the UI verification screenshot.
- **Test login** — the login mechanism used to reach an authenticated view for the screenshot (for app-main this is a scoped `login-with-playwright` skill + seeded creds; that helper stays per-repo — reference it via this key, don't inline any repo's credentials).
- **Plans store (central)** — `~/code/plans/<repo>/{open,completed}/<topic>/`, where `<repo>` is the value of the working repo's `Plans bucket` seam key (a missing key means STOP and report it; plans about `~/code` itself use the literal bucket `stronghold`) and `<repo-dir>` is the working repo's directory name (the basename of its primary clone under `~/code`), which is what is passed to `wt-resolve.sh` and `plan-folder.sh` and used in `~/code/<repo-dir>` paths. Active plans live under `open/<topic>/`; finished topics under `completed/<topic>/`. Per-plan `reviews/`, `research/`, `mocks/`, `screenshots/`, and transient `tmp/` are subfolders of the plan's `<topic>/` dir.

If a key is absent (seam not fully rolled out for that repo), degrade gracefully: fall back to the ambient value and note the assumption rather than erroring.

## Branch Guard

Resolve `<worktree>` and `<plan-branch>` **once**, before delegating any step, by running `~/code/.claude/scripts/wt-resolve.sh <repo-dir> --plan <plan-file> [--assert]` (CLAUDE.md → Worktree Discipline, part (1); it reads the plan's `branch:` and `worktree:` frontmatter, verifies them against `git worktree list --porcelain`, and prints JSON with `kind`, `worktree_abs`, `worktree_rel` and `problems`; the script header documents the rules). This skill never runs `git checkout` or `git switch`.

1. **Plan has `branch:`:** use the script's `<worktree>` (`kind=linked` gives the worktree path; `kind=primary` the primary clone `~/code/<repo-dir>`; stronghold and Default-branch plans never get a worktree, so `<worktree>` is `~/code/<repo-dir>`, or `~/code` for `repo: stronghold`). If `kind=none` (the branch is checked out nowhere), create it via `make wt-new REPO=<repo-dir> BRANCH=<plan-branch>` from `~/code` (own Bash call, orchestrator only), then re-run the script; `has no worktree support` means silently fall back to the primary clone, any other failure means STOP and report. Write `worktree:` back into the plan frontmatter when found or created.
2. **Plan has no `branch:` (older plan)** and the primary clone is on the repo's **Default branch** (`main`/`master`): the plan has no feature branch yet. Follow `/plan-creator`'s Branch Guard (fetch `origin/<default>`, confirm via `AskUserQuestion`, `make wt-new`, the `has no worktree support` fallback) before delegating, then write `branch:` and `worktree:` into the frontmatter. If the primary is on a feature branch, `<plan-branch>` is that branch and `<worktree>` is `~/code/<repo-dir>`; backfill `branch:`.
3. **After resolving (any case), assert** with `git -C <worktree> branch --show-current` (use bare `git ...` instead when `<worktree>` is already the cwd (e.g. `~/code` for stronghold plans) — a redundant `-C` is blocked by a hook) that it equals `<plan-branch>`; on a mismatch STOP and report back (`wt-resolve.sh --assert` does the same check; it exits 3 on a mismatch). Never check out. A stale `worktree:` value (the script reports `stale-worktree`) is overwritten with the script's `worktree_rel`, or dropped when the plan has no worktree.

Expand `~` in `<worktree>` to the full absolute home path before injecting it into any prompt (Edit/Write do not expand `~`).

## Sandbox Rules

- **Git commands** (`git add`, `git commit`, `git status`, `git diff`, etc.): run in **default sandbox** — never use `dangerouslyDisableSandbox`
- **Container runtime commands** (the repo's docker/container/build/`make`-style targets): use `dangerouslyDisableSandbox: true` (the Docker socket / container runtime requires it)
- **`grep`**: read-only — run **bare and sandboxed** (`grep ...` as the first token, no compounding/`awk`/`sed` wrapper); **never** set `dangerouslyDisableSandbox` on grep (it forces an un-suppressible prompt; the bare form is allowlisted via `Bash(grep:*)`)
- Subagent prompts must include these rules so subagents follow them too

## Workflow

### Step 1: Locate and Read the Plan

- **Argument forms:** `<plan-name>` or `<repo>/<plan-name>`. A `<repo>/` prefix (a `Plans bucket` value, e.g. `app-main/soft-delete-trash-api`) fixes the working repo regardless of session context: it is the `~/code/<dir>` whose `Plans bucket` equals the prefix (identical to the directory name in every current repo), or `~/code` for `stronghold`. Match only the part after the slash as the plan name below.
- Resolve the plan in the central **Plans store**: take `<repo>` from the working repo's `## Claude Config` `Plans bucket` key (plans about `~/code` itself use `stronghold`). Then find the plan matching **$ARGUMENTS** at `~/code/plans/<repo>/open/<topic>/<name>.md` — search `open/` FIRST, then `~/code/plans/<repo>/completed/<topic>/<name>.md`
- Derive `<topic>` from the plan file's parent directory name (e.g., if the plan is at `~/code/plans/<repo>/open/auth/login-flow.md`, `<topic>` is `auth`)
- Get scope and progress with `~/code/.claude/scripts/plan-status.sh <repo>/<plan-name>` (plan path, frontmatter, steps with checkbox progress, next incomplete step). The orchestrator does not read the whole plan: each executor reads its own step.
- Total steps and complete-vs-remaining come from that output's `progress:` line and per-step `done` column
- **Resolve `<plan-branch>` and `<worktree>`** per the Branch Guard above, **before** delegating any step. Subagents must never create a branch or worktree.
- Report to user: "Found N remaining steps out of M total. Starting from Phase/Step X (branch `<plan-branch>`, worktree <worktree-or-primary>)."

### Step 2: Execution Loop

For each remaining incomplete step, the main agent orchestrates:

#### 2a. Spawn Execution Subagent

Launch a subagent via the Agent tool with `model: "sonnet"` and this prompt pattern (the executor's own validation, review and fix sub-subagents inherit that model unless `/next-step-taker` pins one):

```
Run /next-step-taker for the plan "<plan-name>".

The next incomplete step is Step N: <step name>.

WORKTREE DISCIPLINE (critical): The working branch for this plan is `<plan-branch>`, checked out at `<worktree>`. Before reading or editing any file, running a build/test, or committing, run `git -C <worktree> branch --show-current` (bare `git branch --show-current` when `<worktree>` is already the cwd, e.g. `~/code` for stronghold plans — a redundant `-C` is blocked by a hook). Bash cwd does not persist between calls: use `git -C <worktree>` for every git command and absolute paths under `<worktree>` for everything else (builds/tests: `make -C <worktree> <target>` or `--project-directory <worktree>`, never a prior `cd`). If it is **not** `<plan-branch>`, **STOP and report back** to the orchestrator. **NEVER** run `git checkout`, `git switch`, or any command that creates a branch or worktree (`git checkout -b`, `git branch <name>`, `git worktree add`) to fix it. Edit sub-repo files only by absolute path under `<worktree>`; plans, reviews and the changelog stay under `~/code/plans` and `~/code/changelog`. Whatever the reason the branch is wrong (including `<plan-branch>` being checked out in another worktree), STOP and report back; never retry, force, or create a new branch. (Canonical rule: CLAUDE.md → Worktree Discipline.)

Important overrides for this run:
- Do NOT pause at the end to ask the user — complete the full workflow (execute, validate, review, fix, update tracking) and return your final report.
- Follow all CLAUDE.md guidelines.
- Context budget (every turn re-reads your whole context): read the plan ONLY via `~/code/.claude/scripts/plan-status.sh <repo>/<plan-name> --step N --with-context`, then `Read` with offset/limit on your step's lines before ticking checkboxes; never `cat`/`sed`/`Read` the whole plan. Send any command output you expect to exceed ~50 lines to a `/tmp/claude/...` file and read it with a limit or `grep -m`; batch independent reads into one message.
- Use dangerouslyDisableSandbox: true for the repo's Container-runtime commands only (docker/container/build/make-style targets). Never for git commands.
- CRITICAL: Never dismiss a test failure as "pre-existing" or "flaky" because the test file wasn't modified on this branch. Current changes can break tests indirectly (shared fixtures, CSS/selector changes, templates, timing, imports). For every failure: (1) read the traceback, (2) check if branch changes could affect the failing path, (3) fix if related, (4) if unrelated to the branch, rerun in isolation 2-3 times to characterize it, then HARDEN the test against the most plausible race (readiness wait, deterministic interaction) even when it won't reproduce or the cause is uncertain — never stop at "report" or re-run-and-hope.
```

The subagent handles the entire next-step-taker workflow internally:
1. Execute the step (read files, implement changes)
2. Validate (build, tests via its own sub-subagents)
3. Review pipeline (2 parallel review subagents + fix subagent)
4. Update plan tracking (checkmarks, dates)
5. Return its final report (what changed, validation results, review findings)

#### 2b. Evaluate Subagent Result

The main agent reads the subagent's report and decides:

**Auto-continue** when ALL of these are true:
- Validation passed (build clean, tests green)
- Review pipeline returned all PASS or all findings were fixed
- No unresolved items requiring user decisions

**Stop and ask** when ANY of these are true:
- Test failures that couldn't be auto-fixed
- Review has UNRESOLVED findings (require user decision)
- Fix subagent returned `VALIDATION: FAIL`
- The step's plan instructions were ambiguous
- The plan's `finished` flag was set to `true` (all steps done)

#### 2c. Smoke Test After UI-Affecting Steps

If the completed step changed frontend code (JS, templates, CSS) or test locators/selectors, run a quick UI smoke test against built assets before committing, directly from the orchestrator (no subagent): `~/code/.claude/scripts/run-suite.sh --expect-branch <plan-branch> <worktree> <make target of the repo's UI tests command> [VAR=value ...]`, scoped to a small subset (a marker or low worker count; Commands table → UI tests). It prints a compact PASS/FAIL result with the failing test ids and keeps the full output file only on failure.

If the smoke test fails, enter the **Test Fix Loop** (Section 2e) before committing. If it passes, proceed to commit.

#### 2d. Commit (via subagent)

Spawn a **commit subagent** (Agent tool with `model: "sonnet"`: staging, a commit message and hook-fix loops are mechanical, and unpinned commit agents otherwise inherit the session's Opus) to handle the entire commit workflow. The main agent must never load or execute `/git-commit` itself — doing so pulls staging, diff analysis, message drafting, and pre-commit hook fix loops into the main context window.

```
Commit the current changes using /git-commit.

WORKTREE DISCIPLINE (critical): The working branch for this plan is `<plan-branch>`, checked out at `<worktree>`. Before reading or editing any file, running a build/test, or committing, run `git -C <worktree> branch --show-current` (bare `git branch --show-current` when `<worktree>` is already the cwd, e.g. `~/code` for stronghold plans — a redundant `-C` is blocked by a hook). Bash cwd does not persist between calls: use `git -C <worktree>` for every git command and absolute paths under `<worktree>` for everything else (builds/tests: `make -C <worktree> <target>` or `--project-directory <worktree>`, never a prior `cd`). If it is **not** `<plan-branch>`, **STOP and report back** to the orchestrator. **NEVER** run `git checkout`, `git switch`, or any command that creates a branch or worktree (`git checkout -b`, `git branch <name>`, `git worktree add`) to fix it. Edit sub-repo files only by absolute path under `<worktree>`; plans, reviews and the changelog stay under `~/code/plans` and `~/code/changelog`. Whatever the reason the branch is wrong (including `<plan-branch>` being checked out in another worktree), STOP and report back; never retry, force, or create a new branch.

Commit ONLY the sub-repo source changes; do NOT stage stronghold files under ~/code/plans or ~/code/changelog.

Follow all CLAUDE.md guidelines.
Never use dangerouslyDisableSandbox for git commands.
```

**Why a subagent:** `/git-commit` involves reading diffs, generating messages, and potentially multiple pre-commit fix iterations — all of which pollute the orchestrator's context if run inline. The subagent absorbs this work and returns only a short summary.

#### 2e. Test Fix Loop (when tests fail)

When any test run (smoke test, final suite, or step validation) reports failures:

1. **`run-suite.sh`** (run by the orchestrator) keeps the full output in `/tmp/claude/suite-*.txt` when a run fails and prints the failing test ids
2. **Main agent reads that output file** (the path is printed) to understand failure count and patterns
3. **Fix subagent** is spawned with:
   ```
   WORKTREE DISCIPLINE (critical): The working branch for this plan is `<plan-branch>`, checked out at `<worktree>`. Before reading or editing any file, running a build/test, or committing, run `git -C <worktree> branch --show-current` (bare `git branch --show-current` when `<worktree>` is already the cwd, e.g. `~/code` for stronghold plans — a redundant `-C` is blocked by a hook). Bash cwd does not persist between calls: use `git -C <worktree>` for every git command and absolute paths under `<worktree>` for everything else (builds/tests: `make -C <worktree> <target>` or `--project-directory <worktree>`, never a prior `cd`). If it is **not** `<plan-branch>`, **STOP and report back** to the orchestrator. **NEVER** run `git checkout`, `git switch`, or any command that creates a branch or worktree (`git checkout -b`, `git branch <name>`, `git worktree add`) to fix it. Edit sub-repo files only by absolute path under `<worktree>`; plans, reviews and the changelog stay under `~/code/plans` and `~/code/changelog`. Whatever the reason the branch is wrong (including `<plan-branch>` being checked out in another worktree), STOP and report back; never retry, force, or create a new branch.

   Fix the following UI/integration test failures. The full test output is at:
     <path-to-temp-file>
   Read this file to understand all failures, then fix them.
   <include user decisions if any were provided>
   CRITICAL: Never dismiss a failure as "pre-existing" — check if branch changes could affect the failing path indirectly (shared fixtures, CSS, templates, timing, imports). If unrelated, rerun in isolation 2-3 times to characterize it, then harden the test against the most plausible race even if it won't reproduce or the cause is uncertain.
   After fixing, run the repo's Build command and JS/unit tests command (from its Commands table) to verify JS changes.
   Do NOT run the full test suite — just implement fixes.

   CRITICAL: Every Bash call that runs a Container-runtime command (docker/container/build/make-style target) MUST set dangerouslyDisableSandbox: true.
   Example:
     Bash(command: "<repo Build command> > \"/tmp/claude/build.txt\" 2>&1", dangerouslyDisableSandbox: true)
   ```
4. **Re-run the same `run-suite.sh` command** (a new output file is written on failure)
5. **Repeat** up to 3 iterations. If failures persist after 3 rounds, stop and report to user.
6. **Clean up temp files** — delete all temp test output files once tests pass or the loop exits.

### Step 3: Completion

When all steps are done or the plan is marked finished:

1. **Determine frontend scope.** Run `git -C <worktree> diff origin/<default-branch>...HEAD --name-only` (bare `git` when `<worktree>` is the cwd, per the Branch Guard; using the repo's **Default branch**) to check whether the branch touched any JS, template, or CSS files. Store this as `<touched-frontend>` — it gates both the UI subagent below and the screenshot step.
2. **Run the full test suite via `run-suite.sh`** — sequentially, never simultaneously:
   - Read capacity first (`make -C ~/code wt-capacity REPO=<repo-dir>`, its own Bash call, output redirected to `/tmp/claude/wt-capacity-<repo-dir>.txt`, then Read it and delete it); if `suites_ceiling` is 0, AskUserQuestion (never a sleep loop) before the final suite; if `suites_ceiling` is `n/a` there is no gate, proceed.
   - Run the integration suite directly: `~/code/.claude/scripts/run-suite.sh --expect-branch <plan-branch> <worktree> <make target of the repo's **Integration tests** command> [VAR=value ...]` (no subagent: it prints a compact result and keeps the full output file only on failure).
   - After it completes, if `<touched-frontend>` is true, run the UI suite the same way (the make target of the repo's **UI tests** command). If `<touched-frontend>` is false, skip this and note "UI suite skipped — no frontend files changed" in the final report.
   - **Always use the repo's declared UI tests command** when the UI subagent runs — for some stacks this must run against pre-built assets, never a dev server (use whatever "built" UI target the repo's Commands table specifies).
   - `run-suite.sh` is declared in `sandbox.excludedCommands`, so no `dangerouslyDisableSandbox` is needed. A Commands-table entry that is not a single `make` target (for example `a && b`) is run as separate `run-suite.sh` calls.
   - Read the printed result to decide pass/fail; Read the kept output file only for the failures it names.
   - **Investigate every failure** — never dismiss a failure as "pre-existing" or "flaky" because the test file wasn't modified on this branch. Current changes can break tests indirectly (shared fixtures, CSS/selector changes, templates, timing, imports). For each failure: read the traceback, check if branch changes could affect the failing path, and fix it. If it's unrelated to the branch (isolation reruns 2-3 times), still harden the flaky test (see CLAUDE.md → Test Failures) — never leave it as-is.
3. If failures exist, enter the **Test Fix Loop** (Section 2e).
4. **UI Verification Screenshot.** If `<touched-frontend>` is true, do the following before the final report; otherwise skip to the next item. Capture and provide a Playwright screenshot of the **actual built feature** so the user sees the rendered result, not just a green suite. This catches what tests miss (e.g. CSS that compiles and passes assertions but renders invisibly).
   - The UI-tests run above already rebuilt and brought up the stack with built assets, so the app is live at the repo's **App URL** — capture against that state.
   - Use the repo's **Test login** mechanism to reach an authenticated view (for app-main this is the scoped `login-with-playwright` skill). Capture the key state(s) of the change (e.g. open AND closed for a toggle/sheet) at **both a desktop viewport (1280px wide) and a mobile viewport (420px wide), always** — never desktop only, even when the feature is not mobile-specific.
   - Save under `~/code/plans/<repo>/open/<topic>/screenshots/` (gitignored), named `<state>-desktop.png` / `<state>-mobile.png`. Do NOT send them yet: they are sent in Step 6, after the Handoff block, so they are the very last thing the user receives.
   - The image MUST be of the implemented feature, NOT the upfront design mock. If the app cannot be brought up, say so explicitly in the final report rather than silently skipping.
5. **Clean up** all temp test output files.
6. **Delete all files in the plan topic's `tmp/`** — this is the subagent communication directory created during the run; it is not needed after completion. Note: when the final step set the plan `finished: true`, next-step-taker performs the lifecycle move — a `git mv` of the WHOLE `~/code/plans/<repo>/open/<topic>/` → `~/code/plans/<repo>/completed/<topic>/` (topic-as-unit). Expect the topic dir (and its `tmp/`) to now live under `completed/<topic>/`; resolve the `tmp/` path accordingly.
7. Report final summary:

```
Plan "<name>" — COMPLETE

Steps completed this run: X
Total steps: Y
Test results: integration PASS/FAIL, UI PASS/FAIL/SKIPPED (no frontend changes)

All changes committed.
```

The exact `/git-push` command follows this report in the final Handoff block (after Step 4's sync).

If failures persist after the fix loop, report them and stop for user guidance.

### Step 4: Sync to Stronghold (final step — always run)

After the execution loop finishes (all steps done or plan marked finished) and the completion report is produced, commit + push the stronghold plans-store / changelog changes to `main` via the central sync script (see `~/code/CLAUDE.md` → "Stronghold Auto-Sync (`.claude/scripts/stronghold-sync.sh`)"):

- Run `.claude/scripts/stronghold-sync.sh "<MESSAGE>"` — it is declared in `sandbox.excludedCommands`, so it runs unsandboxed for the SSH push automatically. Do **not** pass `dangerouslyDisableSandbox` (the DDS flag forces an un-suppressible permission prompt; the exclusion achieves the same unsandboxed run silently).
- `<MESSAGE>` = `plans(<repo>): run-plan <plan-name> tracking update` — fill in the resolved `<repo>` (the `Plans bucket` value) and the `<plan-name>` run this session.
- The script operates on the stronghold (`~/code`) regardless of your current working directory, stages only `plans/ changelog/ .claude/ CLAUDE.md` etc. (never a blanket add), and **skips cleanly if nothing changed** — so it is always safe to call even if this run wrote nothing to the stronghold.
- This runs **once at the very end**, not per step. Each delegated `/next-step-taker` may itself call this same sync as its own final step; because the script is idempotent (it skips when nothing is staged), that redundancy is intentional and this end-of-run call is a cheap no-op if the per-step syncs already pushed everything.
- Do NOT open a PR or feature branch for this — the stronghold pushes direct to `main`. (This syncs only the stronghold; the sub-repo code changes committed during the run keep their own normal git-commit/git-push/PR flow.)

### Step 5: Handoff (final text output — always the last thing printed; only when the run completed; Step 6 then sends the screenshot files)

When the run ended COMPLETE (not on a blocker — then print the blocker report instead), print this block from values resolved this run (no extra tool calls):

```
Next step — plan executed and committed. Copy/paste:

  /git-push <repo-dir>:<plan-branch>

Repo: <repo-dir> · Branch: <plan-branch> · Worktree: <worktree-or-primary-clone> · Plan: ~/code/plans/<repo>/<open|completed>/<topic>/<plan-name>.md
Tip: /clear first — the commits and the plan file hold all state, so a fresh context loses nothing and cuts token spend.
```

`<repo-dir>` is the working repo's directory, `<plan-branch>` the branch resolved by the Branch Guard (never the Default branch), `<worktree-or-primary-clone>` the resolved `<worktree>` (as `~/code/...`), `<repo>` the `Plans bucket` value, and the plan path reflects the plan's final location (`completed/<topic>/` when next-step-taker moved the topic after `finished: true`). For `repo: stronghold` plans there is no `/git-push` (the stronghold pushes direct to `main` via the sync above) — print `Done: stronghold changes are already synced to main.` instead.

### Step 6: Send UI Screenshots (the very last action — only when `<touched-frontend>` is true and the run completed)

After the Handoff block is printed, send every desktop and mobile screenshot saved in Step 3.4 with `SendUserFile`, one call per image (desktop then mobile for each state). Nothing else is printed or run after this step. If Step 3.4 could not capture them, the final report already said so; send nothing.

## Key Differences from next-step-taker

| Behavior | next-step-taker | run-plan |
|---|---|---|
| Pause after each step | Always | Only on blockers |
| Auto-commit | No | Yes, via commit subagent |
| Final test suite | No | Yes, runs all tests at end |
| Scope | Single step | All remaining steps |
| Main agent edits code | Never | Never — delegates to subagent |

## Important Notes

- **Main agent is orchestrator only** — never directly edit implementation files, run tests, run container/build commands, or make code changes. ALL execution (code changes, builds, container targets) MUST be delegated to subagents. The one exception is `run-suite.sh`: the orchestrator runs test, lint, typecheck and build targets through it directly, because it returns only a compact result (this includes the final suite in Step 3).
- **Main agent CAN**: read the plan, read temp test output files, run `git -C <worktree> status --short -uall` (bare `git` when `<worktree>` is the cwd) to list changed files, including new untracked ones (`XY path` entries: strip the status prefix, use the new path for a rename), spawn subagents (including commit subagents), re-read the plan between steps, and report progress. **Main agent CANNOT** invoke `/git-commit` directly — it must always be delegated to a subagent.
- **Tests run via `run-suite.sh`, a synchronous Bash call.** It blocks until the make target exits and prints the compact result (an executor subagent running its own validation uses it the same way). For any subagent the orchestrator launched, the Agent-tool reply IS the completion signal. Do not poll the subagent's result file while the subagent is still in flight, do not arm a Monitor on it, and do not reach into a container to inspect process state (`docker compose exec ... pgrep`, raw `ps`, etc.). After the subagent replies, the orchestrator may read `/tmp/claude/test-*.txt` for full output.
- **Each subagent runs the full /next-step-taker workflow** — including its own validation and review sub-subagents. The main agent does not duplicate that work.
- **Test output goes to temp files** — test runner subagents write output to `/tmp/claude/<name>.txt`. The main agent or fix subagent reads from these files. Clean up temp files when no longer needed.
- **Sandbox discipline** — see Sandbox Rules above; include in all subagent prompts.
- If a step modifies the plan itself (e.g., adds sub-steps), the main agent re-runs `plan-status.sh` before continuing.
- When stopping on a blocker, report: which step failed, what was tried, what needs user input.
- **Investigate every test failure** — see the Step 3.1 rule above (never dismiss as "pre-existing"/"flaky"; trace, fix, or confirm unrelated via isolation reruns). Include this rule in all subagent prompts that run or evaluate tests.
