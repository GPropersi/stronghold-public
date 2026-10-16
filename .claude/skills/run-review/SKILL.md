---
name: run-review
description: Execute all pending TODO items from a review file autonomously - each item runs /next-step-taker in review mode in a Sonnet subagent while the orchestrator loops, stopping only on blockers or when all items are done. Use for "run the review", "fix all review items", "apply all review feedback". Argument is the review name (e.g. "/run-review push-review-refactor-foo") or omit to auto-detect from the current branch.
argument-hint: review-name (optional — auto-detects push review from current branch)
---

# Run Review Skill

Autonomously execute all pending TODO items from a review file. The **main agent is an orchestrator only** — it reads the review, delegates each item to a subagent running `/next-step-taker` in review mode, and loops. It never directly edits implementation files, runs tests, or runs build/container commands.

## Repo Config (seam)

This skill is repo-agnostic. Read the **working repo's `## Claude Config`** section (schema in `~/code/CLAUDE.md`; filled-in reference example in `~/code/app-main/CLAUDE.md`) for every repo-specific value, and refer to those keys below rather than hardcoded commands or paths:

- **Container runtime** — how to run containerized/build commands and any shell setup they need.
- **Commands** table — the **Integration tests**, **UI tests**, **JS/unit tests**, and **Build** rows used for validation and the final suite.
- **Plans store (central)** — the central stronghold store where plans and their `reviews/`/transient `tmp/` directories live: `~/code/plans/<repo>/open/<topic>/` (search `completed/<topic>/` too). `<repo>` is the value of the working repo's `Plans bucket` seam key (a missing key means STOP and report it; plans about `~/code` itself use `stronghold`), and `<repo-dir>` is the working repo's directory name (the basename of its primary clone under `~/code`), which is what is passed to `wt-resolve.sh` and used in `~/code/<repo-dir>` paths. Read both from the working repo's `CLAUDE.md` at runtime — never hardcode a repo name.

If a key is absent (seam not fully rolled out for that repo), degrade gracefully: fall back to the ambient value and note the assumption rather than erroring.

## Branch Guard

Resolve the checkout once with `~/code/.claude/scripts/wt-resolve.sh <repo-dir> --branch <current-branch> [--assert]` (`<current-branch>` = the branch read in Step 1; the script header documents the rules). Consequences: `kind=none` means the branch is checked out nowhere, so create it only via `make wt-new REPO=<repo-dir> BRANCH=<current-branch>` from `~/code` (own Bash call, after the user confirms), never a checkout; an `--assert` mismatch means STOP and report back; `has no worktree support` means use the primary clone `~/code/<repo-dir>`.

1. If on the repo's **Default branch** (or another protected trunk such as `main`/`master`): warn the user — review fixes should be on a feature branch. Ask before proceeding. (Note: this guard intentionally does NOT run `gmas` — it only warns; the user may already have an in-flight feature branch's review to apply.)
2. If on a feature branch: proceed.

## Sandbox Rules

- **Git commands** (`git add`, `git commit`, `git status`, `git diff`, etc.): run in **default sandbox** — never use `dangerouslyDisableSandbox`
- **Container runtime commands** (the repo's docker/container/build/`make`-style targets): use `dangerouslyDisableSandbox: true` (the Docker socket / container runtime requires it)
- **`grep`**: read-only — run **bare and sandboxed** (`grep ...` as the first token, no compounding/`awk`/`sed` wrapper); **never** set `dangerouslyDisableSandbox` on grep (it forces an un-suppressible prompt; the bare form is allowlisted via `Bash(grep:*)`)
- Subagent prompts must include these rules so subagents follow them too

## Workflow

### Step 1: Locate and Read the Review

#### If $ARGUMENTS is provided:
- Resolve `<repo>` from the working repo's `Plans bucket` seam key (per **Plans store (central)**), then search the central store for a review file matching **$ARGUMENTS** contextually
- Try `~/code/plans/<repo>/open/<topic>/reviews/<arg>.md`, `~/code/plans/<repo>/open/<topic>/reviews/<arg>-review.md`, and the push-review path `~/code/plans/<repo>/open/<topic>/reviews/push-review-<arg>.md` — searching across all `<topic>/` dirs under `open/`, and falling back to `~/code/plans/<repo>/completed/<topic>/reviews/...` if not found under `open/`

#### If $ARGUMENTS is omitted:
- Get the current branch: `git branch --show-current`. This relies on the invoking session's current working directory (there is no push-review header or plan `branch:` to read yet — the branch is what is being discovered), so `/run-review` with no argument should be run from inside the relevant worktree (or the primary clone when the repo has none); from the stronghold root it would read the stronghold's branch instead. If the branch read is empty, or it is the stronghold's own branch, or no push-review file matches it, ask the user for the argument instead of guessing.
- Resolve `<repo>` from the working repo's `Plans bucket` seam key, then look for the push-review file for that branch in the central store (e.g. `~/code/plans/<repo>/open/<topic>/reviews/push-review-<branch>.md`, then `completed/<topic>/reviews/...`), per **Plans store (central)**

#### Read the review:
- Read the full review file — review files accumulate multiple revision passes and the **latest revision is at the bottom**
- Paginate with `offset` + `limit` if needed to reach the end
- Find the latest revision section (highest `## Review N` for push reviews, latest date/pass for plan reviews)
- Count all unchecked items (`- [ ]`) in the To-Do section(s) of the latest revision (**"To-Do: Required Changes"** for plan reviews; **"To-Do: Mechanical Fixes"** and **"To-Do: Design Decisions"** for push reviews)
- Report to user: "Found N pending items in Review M. Starting from item 1."

If all items are already checked (`- [x]`), report that the review is fully applied and stop.

### Step 2: Execution Loop

For each pending unchecked item, the main agent orchestrates:

#### 2a. Spawn Execution Subagent

Launch a subagent via the Agent tool with `model: "sonnet"` (same executor model as `/run-plan`) and this prompt pattern:

```
Run /next-step-taker in review mode for "<review-name>".

The next unchecked item to apply is item N of M.

Important overrides for this run:
- Do NOT pause at the end to ask the user — complete the full workflow (apply change, validate, review, fix, cross off item) and return your final report.
- Commit the changes using /git-commit before returning. Follow all CLAUDE.md guidelines for commits.
- Follow all CLAUDE.md guidelines.
- CRITICAL: Every Bash call that runs a Container-runtime command (docker/container/build/make-style target) MUST set dangerouslyDisableSandbox: true. Never for git commands.
  Example: Bash(command: "<repo Integration-tests command scoped to a marker> > \"/tmp/claude/test-results.txt\" 2>&1", dangerouslyDisableSandbox: true)
- CRITICAL: Never dismiss a test failure as "pre-existing" or "flaky" because the test file wasn't modified on this branch. Current changes can break tests indirectly (shared fixtures, CSS/selector changes, templates, timing, imports). For every failure: (1) read the traceback, (2) check if branch changes could affect the failing path, (3) fix if related, (4) if confirmed unrelated, rerun in isolation 2-3 times and report findings.
```

The subagent handles the entire workflow internally:
1. Read the review file and find the next unchecked item
2. Apply the change to the codebase
3. Validate (build, tests via its own sub-subagents)
4. Review pipeline (3 parallel review subagents + fix subagent)
5. Cross off the item in the review file (`- [ ]` -> `- [x]`)
6. Commit all changes (implementation + review file update) using `/git-commit`
7. Return its final report (what changed, validation results, review findings)

#### 2b. Evaluate Subagent Result

The main agent reads the subagent's report and decides:

**Auto-continue** when ALL of these are true:
- Validation passed (build clean, tests green)
- Review pipeline returned all PASS or all findings were fixed
- Commit succeeded
- No unresolved items requiring user decisions

**Stop and ask** when ANY of these are true:
- Test failures that couldn't be auto-fixed
- Review has UNRESOLVED findings (require user decision)
- Fix subagent returned `VALIDATION: FAIL`
- The review item was ambiguous or required a decision
- Commit failed (pre-commit hook issues that couldn't be resolved)

#### 2c. Smoke Test After UI-Affecting Items

If the completed item changed frontend code (JS, templates, CSS) or test locators/selectors, spawn a **smoke test subagent** before continuing:

```
Run a quick UI smoke test against built assets. Execute the repo's UI tests command
(from its `## Claude Config` → Commands → UI tests) scoped to a small subset (e.g. a
low parallelism worker count).
Write the full test output to /tmp/claude/smoke-test-review-item-N.txt.
Report: total passed, total failed. If failures, include test names and error summaries.

CRITICAL: Every Bash call that runs a Container-runtime command (docker/container/build/make-style target) MUST set dangerouslyDisableSandbox: true.
Example:
  Bash(command: "<repo UI-tests command, small scoped subset> > \"/tmp/claude/smoke-test-review-item-N.txt\" 2>&1", dangerouslyDisableSandbox: true)
```

If the smoke test fails, enter the **Test Fix Loop** (Section 2e) before continuing.

#### 2d. Re-read the Review File

After each item, re-read the review file to:
- Confirm the item was crossed off
- Count remaining unchecked items
- Report progress: "Completed item N of M. X items remaining."

#### 2e. Test Fix Loop (when tests fail)

When any test run (smoke test or validation) reports failures:

1. **Test runner subagent** writes full output to a temp file (`/tmp/claude/<descriptive-name>.txt`)
2. **Main agent reads the temp file** to understand failure count and patterns
3. **Fix subagent** is spawned with:
   ```
   Fix the following test failures. The full test output is at:
     <path-to-temp-file>
   Read this file to understand all failures, then fix them.
   <include user decisions if any were provided>
   CRITICAL: Never dismiss a failure as "pre-existing" — check if branch changes could affect the failing path indirectly (shared fixtures, CSS, templates, timing, imports). If confirmed unrelated, rerun in isolation 2-3 times to verify flakiness.
   After fixing, run the repo's Build command and JS/unit tests command (from its Commands table) to verify JS changes (if applicable).
   Do NOT run the full test suite — just implement fixes.

   CRITICAL: Every Bash call that runs a Container-runtime command (docker/container/build/make-style target) MUST set dangerouslyDisableSandbox: true.
   Example:
     Bash(command: "<repo Build command> > \"/tmp/claude/build.txt\" 2>&1", dangerouslyDisableSandbox: true)
   ```
4. **Spawn a commit subagent** to commit the fixes using `/git-commit`
5. **Re-run test subagent** writes output to a new temp file
6. **Repeat** up to 3 iterations. If failures persist after 3 rounds, stop and report to user.
7. **Clean up temp files** — delete all temp test output files once tests pass or the loop exits.

### Step 3: Completion

When all items are done:

1. **Run the relevant test suite via subagents** — sequentially, never simultaneously:
   - Read capacity first (`make -C ~/code wt-capacity REPO=<repo-dir>`, its own Bash call, output redirected to `/tmp/claude/wt-capacity-<repo-dir>.txt`, then Read it and delete it); if `suites_ceiling` is 0, AskUserQuestion (never a sleep loop) before the final suite; if `suites_ceiling` is `n/a` there is no gate, proceed.
   - Spawn integration test subagent: the repo's **Integration tests** command. Write output to `/tmp/claude/final-integration-results.txt`.
   - After it completes, spawn UI test subagent: the repo's **UI tests** command. Write output to `/tmp/claude/final-ui-results.txt`.
   - **Always use the repo's declared UI tests command** — for some stacks this must run against pre-built assets, never a dev server (use whatever "built" UI target the repo's Commands table specifies).
   - Main agent reads each result file to determine pass/fail.
   - **Investigate every failure** — never dismiss a failure as "pre-existing" or "flaky" because the test file wasn't modified on this branch. Current changes can break tests indirectly (shared fixtures, CSS/selector changes, templates, timing, imports). For each failure: read the traceback, check if branch changes could affect the failing path, and either fix it or confirm it's unrelated by rerunning in isolation 2-3 times.
2. If failures exist, enter the **Test Fix Loop** (Section 2e).
3. **Clean up** all temp test output files. Also delete all files in `~/code/plans/<repo>/open/<topic>/tmp/` (the subagent communication directory for this review run). Derive `<repo>`/`<topic>` from the resolved review file's path in the central store per **Plans store (central)** (the review file lives at `~/code/plans/<repo>/open/<topic>/reviews/`).
4. Report final summary:

```
Review "<name>" — COMPLETE

Items resolved this run: X
Total items: Y
Test results: integration PASS/FAIL, UI PASS/FAIL

All changes committed. Ready for /git-push when you are.
Tip: /clear first — the commits and the review file hold all state, so a fresh context loses nothing and cuts token spend.
```

If failures persist after the fix loop, report them and stop for user guidance.

### Step 4: Sync to Stronghold (final step — always run)

After the execution loop finishes (all review items resolved) and the completion report is produced, commit + push the stronghold plans-store / changelog changes to `main` via the central sync script (see `~/code/CLAUDE.md` → "Stronghold Auto-Sync (`.claude/scripts/stronghold-sync.sh`)"):

- Run `.claude/scripts/stronghold-sync.sh "<MESSAGE>"` — it is declared in `sandbox.excludedCommands`, so it runs unsandboxed for the SSH push automatically. Do **not** pass `dangerouslyDisableSandbox` (the DDS flag forces an un-suppressible permission prompt; the exclusion achieves the same unsandboxed run silently).
- `<MESSAGE>` = `plans(<repo>): run-review <review-name> tracking update` — fill in the resolved `<repo>` (the `Plans bucket` value) and the `<review-name>` run this session.
- The script operates on the stronghold (`~/code`) regardless of your current working directory, stages only `plans/ changelog/ .claude/ CLAUDE.md` etc. (never a blanket add), and **skips cleanly if nothing changed** — so it is always safe to call even if this run wrote nothing to the stronghold.
- This runs **once at the very end**, not per item. Each delegated `/next-step-taker` may itself call this same sync as its own final step; because the script is idempotent (it skips when nothing is staged), that redundancy is intentional and this end-of-run call is a cheap no-op if the per-item syncs already pushed everything.
- Do NOT open a PR or feature branch for this — the stronghold pushes direct to `main`. (This syncs only the stronghold; the sub-repo code changes committed during the run keep their own normal git-commit/git-push/PR flow.)

## Key Differences from Related Skills

| Behavior | next-step-taker | run-review | run-plan |
|---|---|---|---|
| Pause after each item | Always | Only on blockers | Only on blockers |
| Commits | No | Yes, inside execution subagent | Yes, via commit subagent |
| Final test suite | No | Yes | Yes |
| Scope | Single item | All pending review items | All remaining plan steps |
| Main agent edits code | Never | Never | Never |
| Input | Plan or review | Review file only | Plan file only |

## Important Notes

- **Main agent is orchestrator only** — never directly edit implementation files, run tests, run container/build commands, or make code changes. ALL execution (code changes, tests, builds, container targets) MUST be delegated to subagents.
- **Main agent CAN**: read the review file, read temp test output files, run `git status --short -uall` (from inside the relevant worktree, as in Step 1; unlike a name-only diff it also lists new untracked files, `XY path` entries: strip the status prefix, use the new path for a rename), spawn subagents, re-read the review between items, and report progress.
- **Commits happen inside the execution subagent** — the subagent runs `/git-commit` as part of its workflow. The main agent does not commit directly.
- **Each subagent runs the full /next-step-taker review mode workflow** — including its own validation and review sub-subagents. The main agent does not duplicate that work.
- **Test output goes to temp files** — test runner subagents write output to `/tmp/claude/<name>.txt`. The main agent or fix subagent reads from these files. Clean up temp files when no longer needed.
- **Tests run via synchronous Bash inside subagents** — the subagent invokes the repo's test commands with the synchronous `Bash` tool (and `dangerouslyDisableSandbox: true`), blocks until the command exits, and reports the result. The orchestrator waits for the subagent's Agent-tool reply — that reply IS the completion signal. Do not arm a Monitor on the result file, do not poll a running subagent, and do not reach into a container with a side-channel probe.
- **Sandbox discipline** — git commands use default sandbox; Container-runtime commands (docker/container/build/`make`-style targets) use `dangerouslyDisableSandbox: true`; `grep` runs bare and sandboxed (never `dangerouslyDisableSandbox` on grep — it forces an un-suppressible prompt). Include this rule in all subagent prompts.
- **Review item ordering** — process items in the order they appear in the review file. Do not reorder or parallelize, as later items may depend on earlier fixes.
- When stopping on a blocker, report: which item failed, what was tried, what needs user input.
- **Investigate every test failure** — never dismiss a failure as "pre-existing" or "flaky" because the test file wasn't modified on this branch. Current changes can break tests indirectly (shared fixtures, CSS/selector changes, templates, timing, imports). For each failure: read the traceback, check if branch changes could affect the failing path, and either fix it or confirm it's unrelated by rerunning in isolation 2-3 times. Include this rule in all subagent prompts that run or evaluate tests.
