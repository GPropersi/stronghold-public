---
name: plan-creator
description: Creates structured planning documents for new features or tasks in the central stronghold plans store (~/code/plans/<repo>/open/<topic>/). Use when the user asks to create, write, or draft a plan for a feature, task, or implementation. Performs deep codebase research using parallel subagents before writing the plan to ensure accuracy.
---

> **Repo-agnostic.** This skill reads the working repo's `## Claude Config` section (schema in
> `~/code/CLAUDE.md`) for every repo-specific value — **Repo slug**, **Default branch**,
> **Token generator**, **Plans store (central)**, **Commands** table, **Issue labels**, **GitHub project
> board**, **PR reviewer**, and **Stack**. Refer to those keys by name; never hardcode a repo's literals.
> If a repo omits a key, degrade gracefully: fall back to the ambient value and note the assumption.

> **Central plans store (resolve `<repo>` first).** All plans/research/reviews/mocks live in **one tracked
> store in the stronghold** (`~/code/plans/`), never inside the sub-repos. Before any file path below,
> resolve `<repo>` = the value of the working repo's `Plans bucket` seam key (`## Claude Config` in its
> `CLAUDE.md`; the directory name under `~/code/plans/`; a missing key means STOP and report it, no fallback).
> Plans about `~/code` itself use the literal bucket `stronghold`. `<repo-dir>` = the working repo's directory
> name (the basename of its primary clone under `~/code`): it is what is passed to `wt-resolve.sh` and used in
> `~/code/<repo-dir>` paths. Read both from the working repo's `CLAUDE.md` at runtime — never hardcode a repo name. New plans ALWAYS start under
> `~/code/plans/<repo>/open/<topic>/`, with `reviews/`, `research/`, `mocks/`, and transient `tmp/` as
> subfolders of the plan's `<topic>/` dir. Full rules: `~/code/CLAUDE.md` → "Central Plans Store".

> **Resolving the working branch (`<current-branch>`) — the WORKING SUB-REPO's branch, never the stronghold cwd.** Every "current branch" reference below (Step 0's master-file `**Branch:**` match, and the Branch Guard) means the branch of the **sub-repo the plan targets** (`<repo-dir>`), NOT the stronghold. The cwd (`$CLAUDE_PROJECT_DIR`) is always `~/code`, which sits on `main` *by design* — a bare `git branch --show-current` reads the stronghold and would false-fire the Branch Guard on every plan. Resolve `<current-branch>` once via `git -C ~/code/<repo-dir> branch --show-current` (e.g. `~/code/app-main`) and use it everywhere below. If `<repo>` is the literal bucket `stronghold` (a plan about `~/code` itself), the working repo *is* `~/code` and the bare check is correct. Under worktrees this reads the PRIMARY clone, which stays on the Default branch; the plan's own `<plan-branch>` / `<worktree>` are resolved by `~/code/.claude/scripts/wt-resolve.sh` (see Branch Guard below).

## Step 0: Sub-Plan Detection

Before Branch Guard, check whether this is a sub-plan of an existing master plan. A sub-plan inherits its branch and topic folder from a specific step in the master — it does NOT get inferred or asked about.

### Triggers (any one is sufficient)

- User's request contains patterns like `step N of <master>`, `sub-plan (of|for) <master>`, `phase N of <master>`, `next step of <master>`, or explicitly references a file path matching `~/code/plans/<repo>/open/*/*-master.md` (or, for a cross-repo master, `~/code/plans/stronghold/open/*/*-master.md`).
- The current branch is a **non-default** branch (the primary clone sits on the Default branch by design, so a master step with `**Branch:** main` must not false-trigger) and matches a `**Branch:**` value in any master file from the tiered glob in "If triggered" step 1 (`~/code/plans/<repo>/open/*/*-master.md`, then `~/code/plans/stronghold/open/*/*-master.md` when `<repo>` != `stronghold`). Grep for `^\*\*Branch:\*\* \`<current-branch>\`` across those master files.

### If triggered

1. **Locate the master.** Glob the ordered tiers below and **stop at the first tier that contains a match** after disambiguation (a live cross-repo master must not be shadowed by a stale finished one, so tier 2 precedes `completed/`; a tier that has files but no match falls through to the next): (1) `~/code/plans/<repo>/open/*/*-master.md`; (2) only when `<repo>` != `stronghold`, `~/code/plans/stronghold/open/*/*-master.md`; (3) `~/code/plans/<repo>/completed/*/*-master.md`; (4) `~/code/plans/stronghold/completed/*/*-master.md`. Within a tier, disambiguate by filename match to user's reference first, then by branch match; if still ambiguous use `AskUserQuestion` to let the user pick.
2. **Resolve the target step.** If the user said "Step N", extract the number. Else match by description or by `**Branch:**` equal to the current branch.
3. **Require a `**Branch:**` field** on that step (backtick-quoted). If missing, error out:
   > "Master plan `<path>` Step N has no `**Branch:**` field. Regenerate with `/master-plan-creator` or add the field manually before creating the sub-plan."
4. **Extract `<target-branch>`** from the backticks.
5. **Derive `<topic>`** — the last path segment of `<target-branch>` (e.g., `ts/feature-splash` → `feature-splash`, `infra/openapi-typescript-codegen` → `openapi-typescript-codegen`). **Default-branch target:** when `<target-branch>` is the repo's Default branch (e.g. `main`, as for stronghold steps) that segment is just `main`, so instead derive `<topic>` as the kebab-case of the master step's title (preferring an existing `**Sub-plan:**` link target when the step already has one), and set `<no-worktree>=true` (`wt-new` cannot add the default branch).
6. **Store** `<sub-plan-mode>=true`, `<target-branch>`, `<topic>`, `<master-path>`, `<step-number>` (and `<no-worktree>=true` when set above) for use in later steps.

When sub-plan mode is active, Branch Guard and Step 1 use the overrides below; otherwise follow the normal flow.

## Branch Guard

> **Never create a branch or worktree without explicit user confirmation — including from the repo's Default branch.** Under the worktree discipline (`~/code/CLAUDE.md` → Worktree Discipline) each branch gets its own worktree, so creating one touches no one else's checkout (a concurrent `/run-plan` keeps working in its own worktree) — but it still goes through an `AskUserQuestion` the user approves first. This skill never runs `git checkout` or `git switch`, and never switches the primary clone off the Default branch; `make wt-new` (from `~/code`, its own Bash call, never compounded) is the only code path that creates a branch or worktree.

**Default behavior** (not sub-plan mode) — evaluate `<current-branch>` (the PRIMARY clone's branch, resolved in the preamble above via `git -C ~/code/<repo-dir> branch --show-current`), never the stronghold cwd:
0. **`<repo>` is `stronghold`:** skip the fetch, `wt-new` and `wt-resolve.sh` below (use bare `git` for any stronghold git call, never `-C`). No worktree: omit `worktree:`, leave `<plan-branch>` unset (the stronghold works directly on `main`), and proceed.
1. First `git -C ~/code/<repo-dir> fetch origin <Default branch>` (`wt-new` bases a new branch on the local `origin/<default>` ref and does no fetch itself).
2. Then run `~/code/.claude/scripts/wt-resolve.sh <repo-dir> --branch <proposed> [--assert]` to see what exists (the script applies the porcelain rules: a `prunable` block or one whose path is missing counts as absent; its header documents them). Use `AskUserQuestion`, leading with the recommended option: propose a feature branch name based on the task context (e.g., `refactor/splash-validation`, `fix/login-error`) as `<proposed>` (if the script reports `<proposed>` already checked out, i.e. `kind` is `linked` or `primary`, reuse that worktree or ask instead of re-creating it; `kind=none` means nothing holds it, so it is safe to create) and offer:
   - **Create worktree + branch `<proposed>` (Recommended)** — run `make wt-new REPO=<repo> BRANCH=<proposed>` from `~/code`, standalone (`REPO=` takes the `<repo-dir>` value); then read the path back with `wt-resolve.sh <repo-dir> --branch <proposed> --assert` (an assert mismatch means STOP and report). Inform the user which branch and worktree were created.
   - **Use the primary clone as-is (no worktree)** — if `<current-branch>` is the Default branch (`main`/`master`), stay and proceed (no `branch:` record); if it is a non-default feature branch, `<plan-branch>` = that branch and no `worktree:` record.
3. `wt-new` fails with `has no worktree support` (the repo has not adopted worktrees yet): tell the user in one line, fall back to the primary-clone option above, and omit `worktree:`. Any other `wt-new` failure: STOP and surface the stderr; do not retry or improvise a checkout.

**Sub-plan mode override** (when Step 0 set `<sub-plan-mode>=true`) — check in this order:
1. `<no-worktree>=true` (or `<target-branch>` is the repo's Default branch, `main`/`master`): no worktree. `<plan-branch>` = `<target-branch>` and `worktree:` is omitted (`wt-new` cannot add the default branch, which the primary already holds; `repo: stronghold` is never a `wt-new` target either).
2. `~/code/.claude/scripts/wt-resolve.sh <repo-dir> --branch <target-branch>` reports `kind=linked` or `kind=primary`: reuse it (`linked` gives `<worktree>`; `primary` means stay in the primary clone, no `worktree:`). No question needed.
3. Otherwise (`kind=none`) run the `git -C ~/code/<repo-dir> fetch origin <Default branch>` from the default flow, then `AskUserQuestion` — **Create worktree + branch `<target-branch>` (Recommended)** via `make wt-new REPO=<repo-dir> BRANCH=<target-branch>`, or **Use the primary clone as-is (no worktree)**. The `has no worktree support` fallback and other-failure rule above apply unchanged. If the user picks the primary clone while it is not on `<target-branch>` (or after that fallback), still record `branch: <target-branch>` and tell the user the primary is not on it, so downstream assert-and-STOP will fire until they prepare the branch.

**Record the resolved branch as `<plan-branch>` and its checkout as `<worktree>`** (both used in the Step 3 frontmatter). Together they are the single durable record of which sub-repo branch the plan is worked on and where, so downstream skills (`/run-plan`, `/next-step-taker`, `/git-commit`, `/git-push`) reconcile onto it per `~/code/CLAUDE.md` → Worktree Discipline instead of guessing a name from the topic folder:
- Sub-plan mode → `<plan-branch>` = `<target-branch>`.
- Default mode, user created a worktree + branch → `<plan-branch>` = that branch's name.
- Default mode, user chose the primary clone → set `<plan-branch>` = `<current-branch>` **only if it is a non-default feature branch**; if it is the repo's Default branch (`main`/`master`), leave `<plan-branch>` unset and **omit** the `branch:` key from frontmatter (a `branch: main` record would be actively misleading; a worktree branch is created only by `wt-new`, and `/git-push`'s documented exception creates one only for already-committed local commits).
- `<worktree>` is the stronghold-relative `<repo>/.claude/worktrees/<slug>` **read back from `wt-resolve.sh`** (`worktree_rel`), never guessed from the slug rules. When no worktree was created or found (primary clone, Default-branch target, unsupported repo, stronghold plans), leave `<worktree>` unset and **omit** the `worktree:` key.

## Step 1: Determine Topic Folder

**Default behavior** (not sub-plan mode):
- Infer `<topic>` from the user's request (e.g., a request about a data-model change → `models`, about API routing → `api-route`, about OpenAPI → `openapi`; examples illustrative — name it after the feature area).
- If the topic is not obvious, ask: "Which topic folder should this plan go in? (suggest 2-3 existing folders under `~/code/plans/<repo>/open/`, or a new name)".

**Sub-plan mode override** (when Step 0 set `<sub-plan-mode>=true`):
- `<topic>` is already set by Step 0 (from `<target-branch>`, or from the step title for a Default-branch target). Do NOT infer or ask.
- If `~/code/plans/<repo>/open/<topic>/` does not exist, run `mkdir -p ~/code/plans/<repo>/open/<topic>/` (this folder should already exist if the master was generated via `/master-plan-creator`, but create it defensively).

Store the result as `<topic>` for use in all subsequent file paths. Plan, research, and mock paths all live under the repo's **Plans store (central)** — `~/code/plans/<repo>/open/<topic>/` for new plans.

## Step 2: Deep Research via Parallel Subagents

Before writing any plan, perform deep codebase research using parallel subagents. This keeps source file reads out of the main context window and enables thorough, parallel investigation.

### 2a. Identify Research Targets

From the user's feature/task description, identify:
- **Affected modules**: Which backend modules, frontend modules, templates/views, and test directories are involved? (Map to the repo's own layout.)
- **Affected endpoints**: Which routes will be created, modified, or deleted?
- **Affected symbols**: Which functions, classes, schemas, or models will change?
- **Task type flags**: Does this involve data validation or model changes? (adds area 5 to Agent A). Does this create, modify, or remove an HTTP endpoint? (adds area 3 to Agent B). Does this add or modify UI? (launches Agent C)

### 2b. Launch Research Subagents

Read `references/research-prompts.md` for the full prompt definitions and expected JSON response format.

Before launching subagents, create the `~/code/plans/<repo>/open/<topic>/research/` directory (permanent — not `tmp/`; see the Persistent Research Artifacts note below).

Launch subagents **in parallel** using the Agent tool with `subagent_type: "researcher"` and `model: "sonnet"` (`.claude/agents/researcher.md`: read-only tools plus Write and web, ~15k-token startup instead of ~36k). NEVER use `subagent_type: "Explore"` — Explore agents cannot use the Write tool. Each subagent:
- Receives the user's task description, the research targets from 2a, and the path it must write its output to
- Reads source files independently (the main agent does NOT pre-read files)
- Writes its full findings JSON to `~/code/plans/<repo>/open/<topic>/research/research-<focus>.md` (where `<focus>` describes the research area, e.g., `research-architecture.md`, `research-dependencies.md`, `research-request-chain.md`, `research-tests.md`, `research-schemas.md`)
- Returns only a one-line confirmation: `Written to <path>`

Include this preamble in every subagent prompt:

> You are a **research-only** subagent exploring the codebase to inform a detailed implementation plan. Your job is to discover, read, and catalog code — not to edit or create application code.
>
> **How to explore:** Use Glob to find files by pattern, Grep to search for symbols/usages/imports, and Read to examine file contents. Search broadly first (Glob/Grep), then read the specific files you find. Do NOT guess file paths — discover them.
>
> **Task:** `<user's task description>`. Affected modules/files: `<list from 2a>`.
>
> Read the source files relevant to your research area. Write your complete findings to `~/code/plans/<repo>/open/<topic>/research/research-<focus>.md` **using the `Write` tool** (NEVER `cat <<EOF`, `python3 << 'EOF'`, `cat >`, `tee`, `printf >`, `echo >`, or any Bash heredoc/redirect — any heredoc or inline script containing `{` and quotes trips the brace+quote security prompt). Then return only this one-line confirmation: `Written to <path>`. Every file path you cite must be one you actually read.

### Persistent Research Artifacts (feeds `/plan-reviewer`)

Unlike scratch files under `~/code/plans/<repo>/open/<topic>/tmp/`, research files live at `~/code/plans/<repo>/open/<topic>/research/` and are **never deleted** — same lifecycle as `~/code/plans/<repo>/open/<topic>/mocks/`. `/plan-reviewer`'s subagents read the file(s) matching their role as grounding context before touching source, which skips the broad Glob/Grep discovery sweep the research subagents already paid for. This is why Step 2c and Step 3's cleanup below write/leave these files at `research/` instead of `tmp/`.

The six research areas run as **at most three agents**. Areas that read the same source files share one agent, which writes **one file per area**, so plan-reviewer's research-file mapping is unchanged. Give each agent the prompt sections of all its areas from `references/research-prompts.md`, the list of files it must write, and one `Written to <path>` line per file to return.

| Agent | Research areas (prompt sections) | Files written | Launch condition |
|---|---|---|---|
| A — Code map | 1 Architecture & Patterns, 2 Dependency & Impact Mapping, 5 Schema & Data Shapes (5 only when the task involves data validation or model changes) | `research-architecture.md`, `research-dependencies.md`, `research-schemas.md` | Always |
| B — Behavior & verification | 4 Test Infrastructure, 3 Request/Response Chain (3 only when the task creates, modifies or removes an HTTP endpoint) | `research-tests.md`, `research-request-chain.md` | Always |
| C — UX | 6 UX & Interaction Analysis | `research-ux-interaction.md` | Only when the task adds or modifies UI elements |

*Why merged:* areas 1, 2 and 5 read the same modules, and areas 3 and 4 the same routes and tests. Separate agents re-read those files and each paid the ~36k-token agent startup context.

All applicable agents must launch in a **single message** for true parallelism.

### 2c. Collect and Use Research Results

After all subagents return their one-line confirmations:
1. Read each `~/code/plans/<repo>/open/<topic>/research/research-<focus>.md` file to compile findings
2. Parse each JSON response
3. If any subagent fails or its file is missing/invalid, note the gap — do NOT skip the area; instead, read the minimum necessary files directly to fill the gap
4. Use the structured findings to inform plan writing in Step 3 — the main agent should reference subagent findings (file paths, signatures, patterns) rather than re-reading those files

**The research summaries are the foundation of the plan.** Every file path, function signature, data shape, and pattern referenced in to-do items should trace back to a subagent finding.

## Step 3: Plan Creation

1. Create `~/code/plans/<repo>/open/<topic>/<feature-name>.md` (create `~/code/plans/<repo>/open/<topic>/` if missing).
2. Name the file after the feature/task (kebab-case).
3. If research is written as a separate document, it goes to `~/code/plans/<repo>/open/<topic>/research/<doc-name>.md`.
3. Structure the plan as follows (every new plan starts with `repo: <repo>` YAML frontmatter — the authoritative association that survives moves; add `short_title:` (2-5 word plain-English name, max 40 chars, no code or file names; it is the plans dashboard card title); add `short_summary:` (1-2 plain sentences, max 220 chars, quoted, no code: what the plan is trying to do and why; the dashboard shows it when a card opens); add `branch: <plan-branch>` when the Branch Guard resolved one (omit it when `<plan-branch>` is unset — i.e. the plan stayed on the Default branch); add `worktree: <worktree>` when the Branch Guard recorded one (omit it when `<worktree>` is unset, exactly like `branch:`); Step 4e adds the `github_issue*` keys alongside these):

```markdown
---
repo: <repo>
type: plan
short_title: <plain-English name, 2-5 words, max 40 chars, no code or file names; shown on the plans dashboard card>
short_summary: "<1-2 plain-English sentences, max 220 chars, no code, paths or identifiers: what this plan is trying to do and why; shown when the dashboard card opens>"
branch: <plan-branch>
worktree: <repo>/.claude/worktrees/<slug>
---
# <Feature Name>

## Summary
<2-4 sentence description of what this feature does and why.>

## Research Findings
<Brief summary of key discoveries from the research phase that shaped this plan. Include notable patterns, dependencies, or constraints discovered. Keep to 3-5 bullets — this is context for reviewers, not a full research dump.>

## Steps

### 1. <Phase Title>
<One sentence describing what this phase accomplishes.>

**Summary:**
- <2-4 plain-English bullets for a human skimming the plans dashboard>

**To-do:**
- [ ] <Specific, actionable sub-task>
- [ ] <Specific, actionable sub-task>

**Must be true:**
- <Observable behavior this step's tests prove, incl. failure modes, e.g. "restoring a URL whose UTub is deleted returns 409 and leaves it in Trash">

### 2. <Phase Title>
...

## Status
finished: false
```

**Rules:**
- Think like a staff engineer: anticipate edge cases, dependencies, and ordering constraints.
- Each phase must have at least one actionable to-do checkbox.
- **Contracts exact, mechanics to the executor (trial since 2026-10-17).** Spell out exactly what is expensive to get wrong late: endpoints, methods and status codes; request/response and data shapes; DB schema and migrations; signatures that cross module boundaries and their callers in other files; authorization/security rules; UX states (empty, error, mobile); step ordering. Leave out what the executor discovers and its tests or lint catch at once: imports, internal helper names, exact edit locations, which existing test files need key updates, standard verify commands. *Why:* plan reviews kept finding majors in pass 3, mostly mechanical (a missing import, exact-equality tests, a misdescribed helper API), each of which fails on the executor's first test run. Contract bugs (a 404, a universal 400) are the ones that cost a late redesign. Re-measure after 2-3 plans (findings by class per pass, executor fix loops, push-review finds); if push review starts catching contract bugs, move the line.
- **Checkbox marker grammar.** Boxes are `- [ ]`, `- [x]` or `- [~]` (deferred). Write `- [ ] (post-merge) text` only for an item that completes by itself when its PR merges and needs nothing else done. Anything needing an action after merge (deploy, restart, verify in production) is `- [ ] (manual, post-merge) text`, which reconcile never ticks. Do not add a `## Progress Tracking` section; the body boxes are the progress record.
- **Issue ownership.** A sub-plan of a master gets its own distinct issue, never the master's number (the master's number is the umbrella).
- **Every step gets a `**Summary:**` list (the plans dashboard shows it).** 2-4 bullets in plain English: what the step does and why, one short line each, no code, file paths, identifiers or SQL. A reader with no repo context should follow it. Bullets are plain `- ` lines, never checkboxes, and sit before `**To-do:**`.
- **Every step gets a `Must be true:` list.** It names the behaviors the step's tests must prove, failure modes and edge cases included. It is the safety net for detail the plan no longer spells out: the executor must satisfy every item, and the step review panel checks each one is proven by a test.
- The to-do list carries the contract detail; phase descriptions should be brief overviews.
- **Size each step to one executor's context.** Each step is executed by a fresh subagent whose cost grows with every turn (4 weeks of run-plan transcripts: executors with 41+ turns exceeded 150k tokens of context 66–93% of the time, those under 20 turns never did). Aim for roughly 8 files or 20 edits per step and split a larger phase into consecutive steps along natural seams (backend vs frontend, module vs its tests, markup vs behaviour), each leaving the tree green. Do not split just to shrink: steps that depend on each other's in-flight state should stay together, and the final full-verification step stays separate.
- **Ground to-dos in research findings.** Every file path, function signature, constant name, or data shape referenced in a to-do must come from a research subagent's findings. If a subagent didn't cover it, read the file before writing the to-do.
- **Specify exact data structures.** When a to-do item constructs or populates a dict, list, or object, name the exact keys and value types — e.g. (illustrative) `errors["email"] = [USER_FAILURE.EMAIL_TAKEN]`, not "add `EMAIL_TAKEN` to the errors dict." Vague structure descriptions produce silent runtime failures when the implementer guesses the wrong key.
- **No forward references within a step.** If a step's to-do item calls or imports a function, that function must either already exist in the codebase or have been created earlier in the same step. If a function is first defined in step N, no to-do in steps 1–(N-1) may reference it. Check this before finalising step order.
- **Mock every UI change.** If the plan adds or modifies any user-visible UI, you MUST follow the **UI Mockup Protocol** below — produce styled HTML mockup(s) as the visual base *before* finalizing the plan, and link them from the plan. Skipping mockups on a UI plan is not allowed.

**Cleanup:** Nothing to delete for research — `~/code/plans/<repo>/open/<topic>/research/*.md` and `~/code/plans/<repo>/open/<topic>/mocks/` are both permanent plan artifacts and must never be deleted. (There is no more `tmp/research-*.md` to clean up — Step 2b now writes directly to `research/`.)

**Sub-plan cross-link** (sub-plan mode only): after writing the sub-plan file, perform two cross-link operations:

1. **Master → sub-plan link:** Append a cross-link line to the master's Step N. Locate the step's `**To-do:**` block in `<master-path>` and insert a line immediately after the step header (before `**Branch:**`) of the form:

   ```markdown
   **Sub-plan:** [<sub-plan-name>](../<topic>/<sub-plan-name>.md)
   ```

   If the step already has a `**Sub-plan:** none` line, replace it with the link instead of adding a second line. Also set that step's `**Status:**` line to `in-progress` (when it currently starts with `todo`). This makes the master → sub-plan relationship navigable from the master file.

2. **Sub-plan → master backref:** Insert a `**Master plan:**` field into the sub-plan file, immediately after the `## Summary` heading (before the summary text). Derive `<master-parent-topic>` from the master file's parent directory (the directory the master lives in). Format:

   ```markdown
   **Master plan:** [<master-name>](../<master-parent-topic>/<master-name>.md)
   ```

   Where `<master-name>` is the master file's basename without `.md`. When the master is in a different bucket than the sub-plan (a `stronghold` master with a `<repo>` sub-plan), write the backref as the stronghold-relative path instead — `../../../stronghold/<master-state>/<master-topic>/<master>.md` (`<master-state>` is `open` or `completed`, per where Step 0 found the master), relative to `plans/<repo>/open/<topic>/` — so `plan-reviewer`'s primary signal (`**Master plan:**` in the first ~40 lines) still resolves. This is the inverse of the forward-link in the master, and allows the plan-reviewer to auto-detect the master plan via primary signal.

Do not create either link in default (non-sub-plan) mode.

## Stack Protocols (opt-in)

Read the working repo's `## Claude Config` → **Stack** (a hyphenated tag set, e.g. `flask-jinja-vanillajs-vite`). For each `references/stack/<tag>.md` file whose **declared tags intersect the repo's Stack**, load it and apply its protocols. If the repo declares no Stack or none match, apply only the stack-neutral protocols in this SKILL.md.

The stack-neutral protocols below (End-to-End Chain Tracing, Verification Layer Matching phrased via the **Commands** table, Dead-Import Elimination, Function-Signature-Change enumeration, Frontend/Backend Colocation, Accessibility, Cross-Feature Interaction, the UI Mockup Protocol's general fidelity/coverage rules, and the TDD Red→Green→Refactor loop) always apply. Their genuinely stack-bound bodies (dependency pinning, CSRF/token-rendering specifics, framework-specific TDD tooling, framework-specific UI fidelity) live in the stack reference file — for `flask-jinja-vanillajs-vite` that is `references/stack/flask-jinja-vanillajs-vite.md`.

## End-to-End Chain Tracing

For any plan that changes a request/response cycle (route, form, AJAX call, view/template), trace the **full chain** before writing to-dos and include a to-do item for every link that needs changing:

```
Client sends request
  → CSRF / auth middleware (where does the token come from?)
  → Route handler (method, decorators, request parsing)
  → Service layer (and its callees — one level deep)
  → View / response rendering (template or serializer; values passed and their conditional guards)
  → Test fixtures (how does the test obtain CSRF tokens, session state, DB state?)
  → Frontend JS (request data format, field IDs, success/failure handler branches)
```

**Subagent #3 (Request/Response Chain) provides this trace.** Use its findings directly — do not re-trace manually unless the subagent missed an endpoint.

Skipping layers produces plans that break in the gaps between what's described. Common omissions:
- A middleware or decorator that gates the flow (auth condition, CSRF guard) that is only satisfied in some contexts
- A private helper inside a service that also holds the dependency being migrated
- A test fixture that extracts a value (CSRF token, response field) from a rendered response that no longer contains it after a view/template change
- A frontend handler branch that checks a status code or error code that changes after the migration

### Verification Layer Matching

Every step must include a verification command drawn from the repo's **Commands** table (`## Claude Config`). The test type must match the layer changed:

| Change layer | Required verification (map to the repo's **Commands**) | Why |
|---|---|---|
| View / rendered HTML (meta tags, field IDs, conditional blocks) | The repo's **UI tests** command (scoped to the changed module's marker where the repo supports per-marker runs) or a browser/Playwright smoke | Backend request-client calls never parse the DOM; view/template regressions are invisible to integration tests |
| Frontend JS (AJAX format, handlers, DOM reads) | The repo's **JS/unit tests** command + **UI tests** (or Playwright) | JS unit tests catch logic; UI tests catch integration with real DOM |
| Backend only (service, route, DB) | The repo's **Integration tests** command (single-marker variant where available) | Integration tests are sufficient and faster |
| Cross-layer (backend format + JS handler in same step) | **Integration tests** + **JS/unit tests** + **Build** | All three layers must be green |

If a step changes what the browser sees or reads but only runs integration tests, the plan is undertested — add the appropriate UI-level verification. Use the exact command literals the repo declares under **Commands**; do not hardcode another repo's targets.

### Dead Import Elimination Protocol

**Trial (2026-10-17): this is now the executor's duty, not the plan's.** The executor runs lint on every step, which flags dead imports at once, so plans no longer list import removals. The protocol below stays as the executor's checklist.

When any to-do item **deletes a function, class, or helper**, you must trace its import footprint within the same file and include cleanup of every import that becomes dead. This is a mechanical, exhaustive check — not a judgment call.

**Procedure (for every deletion in a step):**

1. **List the symbols the deleted code uses.** Read the function body. Note every name it references that comes from an import at the top of the file — standard-library types, framework functions, project utilities, string constants, model classes, etc. (examples illustrative).
2. **For each such symbol, grep the file for other usages.** Exclude the deleted function(s) and their internal helpers. If no remaining usage exists in the file after the deletion, that import is dead.
3. **Add an explicit removal to-do** for every dead import found. Group them in the same bullet as the deletion (not in a later cleanup step) — the linter's unused-import rule (F401 / `no-unused-vars` class) blocks the commit otherwise.

**Also apply when changing a function's return expression** (e.g., swapping a render/response call for a different one). The old return may have been the sole consumer of an import (a render function, a form/serializer class passed to it, etc.).

**Do not defer dead-import removal to a cleanup step.** If a symbol becomes unused in Step N, it must be removed in Step N. Linters enforce this at commit time — a deferred removal blocks every commit between Step N and the cleanup.

### Function Signature Change Protocol

When any to-do item changes a function's signature (parameter type, name, count, or structure), **use Subagent #2's dependency mapping** to enumerate:

1. **Every private helper inside it** that receives or uses the changed parameter — add a separate to-do to update each one's signature and internal uses.
2. **Every direct call site in other files** — add a to-do for each caller found in the dependency map.

A plan that says "update `public_fn(form)` → `public_fn(value: str)`" without listing `_private_helper(form)` called inside it will produce a runtime error. The callee enumeration must be explicit to-do items, not implied.

### Frontend/Backend Colocation Rule

For every endpoint where the plan changes the request format, response codes, or wire contract, the frontend JS changes **must appear in the same step** as the backend change — never in a later "frontend" or "cleanup" step. This includes (examples illustrative):

- Request serialization format (e.g. `serialize()` → `JSON.stringify(...)`)
- `contentType:` / request headers
- Failure-handler status-code checks (e.g. `xhr.status === 401` → `xhr.status === 400`)
- Error-dispatch keys

Integration tests send the final format directly and will not catch a gap where the browser is silently broken between steps. Write the frontend to-do in the same step bullet block as the backend to-do it pairs with.

### Accessibility Protocol

When a plan adds or modifies any interactive UI element (buttons, inputs, editable regions, expandable panels, modals), include to-do items for each applicable concern:

1. **Keyboard operability**: Every interactive element must be reachable via Tab and activatable via Enter/Space. If a non-button element (e.g., a clickable `<div>` or `<span>`) is made interactive, add `role="button"`, `tabindex="0"`, and keydown handlers for Enter and Space.
2. **Focus management**: After opening/closing an interactive widget (search panel, edit form, modal), specify where focus moves. After closing, focus should return to the element that opened it — track opener identity if needed (e.g., `editOpenedViaKeyboard` flag).
3. **ARIA labels and roles**: Every input must have an `aria-label` or associated `<label>`. Dynamic content changes that the user needs to know about (filter counts, "no results" messages) must use `aria-live="polite"` with a visually-hidden announcement element.
4. **Touch targets**: On mobile (< 992px), interactive elements must have minimum 44px touch targets (`min-height: 2.75rem`). Specify CSS rules with `@media` breakpoints.
5. **Focus visibility**: Interactive elements must show a visible focus indicator. Use `:focus-visible` with `outline-offset` to avoid clipping.

If the plan has no UI changes, skip this protocol.

### Cross-Feature Interaction Protocol

For any plan that adds or modifies an interactive UI feature on a page, **Subagent #6 (UX & Interaction Analysis) inventories all other interactive features on the same page/view.** Use its findings to add to-do items for each composition concern:

1. **State conflicts**: If the new feature hides/shows elements (e.g., search filtering a list), check every other feature that depends on those elements being visible (e.g., card selection, keyboard navigation, inline editing). Add to-do items to handle the conflict (e.g., auto-deselect when selected card is hidden, skip hidden elements in keyboard nav).
2. **Mutual exclusion**: If two features cannot be active simultaneously (e.g., editing a title while a search panel is open), specify which one takes priority and how the other is temporarily disabled or hidden. Add to-do items for the disable/re-enable lifecycle.
3. **Shared event handlers**: If the new feature binds keyboard events (Escape, Enter, arrow keys) that other features also bind, specify precedence and event propagation behavior. Check for `stopPropagation` / `preventDefault` conflicts.
4. **Input performance**: If the feature involves text input that triggers filtering, API calls, or DOM manipulation, add a debounce (typically 150-300ms) with timer cleanup on close/unmount.
5. **Empty/zero states**: For any feature that hides content (search, filters), specify what the user sees when all content is hidden — a "no results" message, a prompt, etc. This must be distinct from the "no content exists" state.
6. **Discoverability**: If the feature makes existing elements interactive in a non-obvious way (e.g., making headers clickable for inline editing), add visual affordances (icons, hover effects) and specify their show/hide rules per device (hover on desktop, persistent on mobile).

## UI Mockup Protocol (Visual Base)

**If the plan adds or modifies any user-visible UI** — a new page, panel, modal, dropdown, button, layout shift, color/spacing change, or on-screen copy — you MUST produce one or more HTML mockups of the proposed result **before finalizing the plan**. The mockup is the visual base the plan is built on; reviewers and the user judge the design from the rendered mock, not from prose. A UI plan with no mockup is incomplete.

If the plan has no user-visible UI change (pure backend, refactor, infra, tests), skip this protocol entirely.

### Where mockups live

- Write each mockup to `~/code/plans/<repo>/open/<topic>/mocks/<descriptive-name>.html` using the **`Write` tool** (create `~/code/plans/<repo>/open/<topic>/mocks/` first). Never use a Bash heredoc/redirect for HTML.
- Mockups are **permanent plan artifacts** — they live alongside the plan, are referenced by it, and are **never** placed in `~/code/plans/<repo>/open/<topic>/tmp/` and **never** deleted during cleanup.

### Fidelity — match the real app, not a generic wireframe

The mock must look like the actual application, with the **correct colors and styling**:

1. **Pull real values from the codebase.** Use Subagent #6's findings, or grep the project's style sources (CSS custom properties / theme variables, stylesheet files, existing template/component markup, and any design-system/utility classes already in use) for the actual hex colors, font families, spacing, border-radius, and class names. **Do NOT invent colors** — every color in the mock must trace to a real value used by the app.
2. **Reuse existing class names and DOM/component structure** where practical so the mock reads like the real rendered page and the implementer can map it directly to the templates/components.
3. **Make each mock self-contained and standalone-renderable** — inline `<style>` (or a `<link>` to the app stylesheet) so the file opens and screenshots on its own with no dev server.

> Stack-specific fidelity guidance (e.g. matching a particular CSS framework's utility classes / brand colors) lives in the opt-in stack reference — for `flask-jinja-vanillajs-vite`, see `references/stack/flask-jinja-vanillajs-vite.md`. Load it per the **Stack Protocols (opt-in)** section.

### Coverage — show the full picture

One static screenshot rarely conveys a design. Produce **multiple mocks (separate files, or multiple labeled states stacked in one file), or a single interactive mock**, whichever communicates the design best:

- Distinct **states** that look different: default, populated/with-data, empty/zero-results, error, loading.
- Distinct **viewports** when layout differs: desktop and mobile (<992px).
- An **interactive mock** (inline `<script>` toggling states, opening the modal, filtering a list) when interaction — not a static frame — is the point of the feature.

State in the plan which mocks cover which states/viewports so nothing is silently unshown.

### Reference from the plan

In the plan's `## Summary` (or the relevant phase), link every mock — `Mockup: [<name>](mocks/<name>.html)` — and ground each UI to-do (colors, class names, layout, copy) in what the mock shows. The mock and the to-dos must agree.

### Render to images for review

After writing the mock files, render each to a PNG so it can be shown to the user (HTML source alone is not "showing pictures"):

1. Start the local mock server once with `make mocks` (from `~/code`; serves `~/code/plans` at `http://127.0.0.1:8799/`, stop with `make mocks-stop`). Then for each mock file, use the Playwright MCP: `browser_navigate` to `http://127.0.0.1:8799/<repo>/open/<topic>/mocks/<name>.html`, then `browser_take_screenshot` saving to `~/code/plans/<repo>/open/<topic>/mocks/<name>.png`. **Do NOT use a `file://` URL** — the pinned Playwright MCP (`@playwright/mcp@0.0.78`) blocks the `file:` protocol outright (`Access to "file:" protocol is blocked`), which is why the http server exists. For interactive mocks, capture each meaningful state (click/toggle, then screenshot) so every state has an image. For responsive mocks, `browser_resize` to a mobile width (e.g. 390px) and capture that too.
2. The generated PNGs are surfaced to the user in the Handoff Message (see below) via `SendUserFile`.
3. **Tear down both halves the moment the last PNG is captured — mandatory, not optional:**
   - `mcp__playwright__browser_close` — closes the headless Chromium.
   - `make mocks-stop` (from `~/code`) — stops the mock server.

   Do this as soon as the captures are done, **before** writing the plan, sending the PNGs, or
   asking the user anything. Do **not** hold the browser open "in case the user wants a revision":
   a revision means re-rendering edited HTML anyway, and re-opening is a sub-second no-op.

   *Why it matters:* `browser_close` is not a page close — it sets `isClose` on the tool result,
   which makes the MCP server call `disposeBackend()` and tear the whole browser down. Nothing else
   ever does: `@playwright/mcp@0.0.78` has **no idle timeout** (its only timeout flags are
   `--timeout-action` / `--timeout-navigation` / `--cdp-timeout`, all per-operation), so an
   un-closed browser lives until the Claude session itself exits. A session parked at a question
   prompt keeps it alive indefinitely — on 2026-09-18 that left a headless Chromium holding
   **1,064 MB across 8 processes for 10 hours**, which is the kind of idle pressure that gets
   tmux windows OOM-killed (see `misc/infra-notes.md`). The next Playwright call after a close
   transparently relaunches the browser, so closing early costs nothing.

## Final Verification Step

For **every plan that touches code or tests**, the last phase must be:

```markdown
### N. Verify All Tests Pass

Run the full test suites to confirm nothing is broken:

**To-do:**
- [ ] Run the repo's **Integration tests** command (from `## Claude Config` → Commands) and confirm all integration tests pass
- [ ] Run the repo's **UI tests** command and confirm all UI/functional tests pass
- [ ] Investigate and fix any failures before marking the plan finished
```

Substitute the repo's actual command literals from the **Commands** table. If the repo declares no UI-test command, drop that line. Do not omit this phase even if only one test file was touched.

## TDD Enforcement

For any plan involving a **new feature or bug fix** (not pure refactoring or cleanup), the steps must follow a strict Red → Green → Refactor loop. Do not bulk-code then bulk-test.

**The Loop (one requirement at a time):**
1. **Red** – Write a focused test for one specific requirement. Run it; confirm it fails with a relevant error.
2. **Green** – Write the minimum code to make that test pass. Run it; confirm it passes.
3. **Refactor** – Clean up for readability and project patterns. Confirm tests stay green.
4. Repeat until the feature is complete.

**Implementation standards:** the framework-specific test-layer choices (which test type for backend vs. frontend logic vs. navigation flows) are **stack-bound** — apply the repo's Stack reference file. For `flask-jinja-vanillajs-vite`, see `references/stack/flask-jinja-vanillajs-vite.md`. Absent a matching stack file, choose test layers by the repo's declared **Commands** and existing test patterns.

## Step 4: Create GitHub Issue

After the plan file is written and cross-linked, create a corresponding GitHub issue and link it to the plan via YAML frontmatter. The issue is the publicly-visible **WHY**; the plan stays the source of truth for the HOW.

All `gh` commands target the repo's **Repo slug** (`## Claude Config`) and authenticate with the repo's **Token generator** — refer to them by key; do not hardcode. In the snippets below, `<Repo slug>` and `<Token generator>` stand for those seam values.

### 4a. Search for existing matches

Extract 2-4 distinguishing keywords from the plan title (drop stopwords like "the", "and", "for", "with"). Search open issues:

```bash
GH_TOKEN=$(<Token generator>) gh issue list \
  --state open --search "<keywords>" --limit 5 \
  --json number,title,labels \
  --repo <Repo slug>
```

If 1+ matches return, present up to the top 3 via `AskUserQuestion` with options labeled `#<N>: <title>` plus a "Create new" option.

- **User picks existing #N:** capture `<issue-number>` and `<issue-url>=https://github.com/<Repo slug>/issues/<N>`. Skip 4b and 4c. Proceed to 4d.
- **User picks "Create new":** proceed to 4b.

If no matches return, skip the question and proceed to 4b directly.

### 4b. Generate issue body and labels

Derive a condensed WHY-focused body in this exact structure:

```markdown
## Problem
<one paragraph: what's broken/missing/unclear today>

## Why
<one paragraph: why this matters now, what prompted it>

## Outcome
<one paragraph: what "done" looks like in observable terms>

---
Plan: `~/code/plans/<repo>/open/<topic>/<feature-name>.md`
```

You (the main agent) just wrote the plan — synthesize Problem/Why/Outcome from the plan's `## Summary` and `## Research Findings` sections. Do not delegate this to a subagent.

Infer **category labels** from plan content, using **only the repo's existing labels** (never invent new ones). Resolve the repo's actual label set at runtime:

```bash
GH_TOKEN=$(<Token generator>) gh label list --repo <Repo slug>
```

Then map plan signals to those labels by the repo's **own module layout** (do not hardcode `backend/`/`frontend/` paths). The table below is an **illustrative** signal→label mapping — match its intent against whichever labels the repo actually has, dropping any signal whose label the repo doesn't define:

| Signal in plan (illustrative)                              | Typical label     |
|-----------------------------------------------------------|-------------------|
| Server-side routes/services/models                         | `backend`         |
| Client-side UI code, templates/views                       | `frontend`        |
| Docker, build tooling, CI workflows, build/deploy          | `Infrastructure`  |
| DB migrations, ORM models, `.sql` files                    | `database`        |
| Test files (any kind)                                      | `testing`         |
| Mobile-specific UI (small-breakpoint, touch targets)       | `mobile`          |
| Desktop-specific UI                                        | `desktop`         |
| New user-visible capability                                | `enhancement`     |
| Defect fix (branch `fix/...` or plan describes a bug)      | `bug`             |

A plan typically gets 1-3 labels. Only pass labels that appeared in the `gh label list` output.

### 4c. Create the issue

```bash
GH_TOKEN=$(<Token generator>) gh issue create \
  --title "<plan-title-verbatim>" \
  --body "<generated-body>" \
  --label "<label1>" --label "<label2>" \
  --repo <Repo slug>
```

Capture the printed URL as `<issue-url>` and parse `<issue-number>`.

**Add to project board + assign bot (defer to `/git-push`'s runtime resolution).** The plan's issue goes on the same project board and gets the same bot assignee that `/git-push` Step 9 applies to PRs. **Never inline any project / status-field / option / bot-node GraphQL IDs** — resolve them **at runtime by name** from the repo's **GitHub project board** name and **Bot identity** login, exactly as the `/git-push` Step 9 `pr-finish.sh` script does for PRs (its implementation is `lib/pr_finish.py` beside it, and its GraphQL files live in `~/code/.claude/skills/git-push/references/graphql/`). First fetch the issue's node ID:

```bash
ISSUE_NODE_ID=$(GH_TOKEN=$(<Token generator>) gh issue view <N> --repo <Repo slug> --json id --jq .id)
```

Then run the `addProjectV2ItemById` mutation and the bot-assignee mutation using the project/bot node IDs resolved at runtime — copy the exact mutation structures **and** the by-name ID-resolution steps from `pr-finish.sh`'s implementation (`lib/pr_finish.py` beside it) and the `.graphql` files in `~/code/.claude/skills/git-push/references/graphql/`. If the repo declares no **GitHub project board** (or no **Bot identity**), skip the board/assignee step entirely.

**Bot-assignee is non-fatal.** If `addAssigneesToAssignable` returns `FORBIDDEN` with `"Assigning agents is not supported with GitHub App installation tokens. Use a user token instead."`, GitHub policy blocks assigning a `[bot]` account via an App installation token — treat it as a **skip, not a failure**. The board item + labels are the substantive integration; the self-assignee is cosmetic. Log a one-line note and continue creating the plan; do NOT abort or retry with a personal token. (Same behavior as `/git-push` Step 9.)

### 4d. Detect master parent, attach as native sub-issue, and back-link

First, resolve which umbrella issue (if any) this sub-plan belongs to. Only a numeric `github_issue:` counts as an umbrella: `github_issue: none` (the explicit waiver) or any invalid/empty/non-numeric value means no umbrella, so **skip this entire step** (no `gh issue view`, no sub-issue attach, no `Part of #` backref).

**Sub-plan mode** (Step 0 set `<sub-plan-mode>=true`): read `<master-path>` frontmatter for `github_issue:` (the umbrella number `<umbrella>`, numeric only) and `github_issue_node_id:` (the umbrella's GraphQL node ID `<umbrella-node-id>`).

**Default mode:** glob `~/code/plans/<repo>/open/<topic>/*-master.md`. If exactly one file exists and its frontmatter has a numeric `github_issue:`, use it. If multiple master files exist or none have a numeric linked issue, **skip this entire step**.

When an umbrella is found, do BOTH of the following:

**1. Attach as a native GitHub sub-issue (the structural parent/child link).** This makes the phase issue a child of the umbrella in GitHub's sub-issue hierarchy. You captured the new phase issue's node ID as `<issue-node-id>` in Step 4c. Resolve `<umbrella-node-id>` from the master frontmatter `github_issue_node_id:`; if that key is absent (older / hand-written master), fetch it:

```bash
GH_TOKEN=$(<Token generator>) gh issue view <umbrella> --repo <Repo slug> --json id --jq .id
```

Write the mutation to a `.graphql` file (never inline — braces+quotes trip the security prompt) and run it via `-F query=@file`:

`~/code/plans/<repo>/open/<topic>/tmp/add-sub-issue.graphql`:
```graphql
mutation {
  addSubIssue(input: { issueId: "<umbrella-node-id>", subIssueId: "<issue-node-id>" }) {
    subIssue { number }
  }
}
```
```bash
GH_TOKEN=$(<Token generator>) gh api graphql -F query=@~/code/plans/<repo>/open/<topic>/tmp/add-sub-issue.graphql
```

If the call errors with an unknown-field / schema error on `addSubIssue`, retry the **same** command with `-H "GraphQL-Features: sub_issues"` appended (the sub-issues schema was preview-gated behind that header before GA). If it still fails, surface the error and the manual command but **do not block plan creation** — the `Part of #N` text link below still records the relationship.

**2. Append the `Part of #N` body backref (human-readable secondary link).** Append `\n\nPart of #<umbrella>` to the new phase issue body via `gh issue edit --body-file`. This keeps a visible link in the issue body and timeline alongside the structural sub-issue relationship.

### 4e. Write issue link to plan frontmatter

Insert the `github_issue*` keys into the existing YAML frontmatter of `~/code/plans/<repo>/open/<topic>/<feature-name>.md` (the `repo: <repo>` key written in Step 3 is already there, above the `# <Feature Name>` H1), preserving the `repo:` key:

```yaml
---
repo: <repo>
branch: <plan-branch>
worktree: <repo>/.claude/worktrees/<slug>
github_issue: <N>
github_issue_url: <url>
---
```

Insert the `github_issue*` keys preserving existing ones (`repo:`, `branch:`, `worktree:` and any others; `branch:` and `worktree:` appear only when Step 3 wrote them). This applies whether 4c created a new issue OR 4a linked to an existing one.

### Failure handling

If `gh issue create` fails (rate limit, transient API error), DO NOT block the plan creation. Surface the error and the manual `gh issue create` command to the user, then exit Step 4. The plan file still exists: write `github_issue: none` into its frontmatter (with a `TODO: create issue and replace none` note in the plan's Summary) so the key is never absent; replace `none` with the real number once the issue is created.

## Handoff Message

After the plan file is written, cross-linked, and the GitHub issue is created, the final user-facing message must:
- **Show the mockups (UI plans only).** If the UI Mockup Protocol produced any mocks, send every rendered PNG to the user with `SendUserFile` (`status: "proactive"`), one call listing all the mock images, with a caption naming which state/viewport each shows. The user judges the design from these images before review. Also note the source HTML path (`~/code/plans/<repo>/open/<topic>/mocks/<name>.html`) in case they want to open it interactively.
- Mention the issue: `Created issue #<N>: <url>` (or `Linked to existing issue #<N>: <url>` if 4a reused one)
- The next step is always `/plan-reviewer` — never `/next-step-taker`; a freshly-written plan must be reviewed before execution begins. Do not print the command here: the final "Next-Command Handoff" block (after the sync, below) prints it last.

## Sync to Stronghold (final step — always run)

When all other work for this invocation is complete, commit + push the stronghold plans-store / changelog changes to `main` via the central sync script (see `~/code/CLAUDE.md` → "Stronghold Auto-Sync (`.claude/scripts/stronghold-sync.sh`)"):

- Run `.claude/scripts/stronghold-sync.sh "<MESSAGE>"` — it is declared in `sandbox.excludedCommands`, so it runs unsandboxed for the SSH push automatically. Do **not** pass `dangerouslyDisableSandbox` (the DDS flag forces an un-suppressible permission prompt; the exclusion achieves the same unsandboxed run silently).
- `<MESSAGE>` = `plans(<repo>): plan-creator <feature-name>` — fill in the resolved `<repo>` (the `Plans bucket` value) and the `<feature-name>` of the plan written this run.
- The script operates on the stronghold (`~/code`) regardless of your current working directory, stages only `plans/ changelog/ .claude/ CLAUDE.md` etc. (never a blanket add), and **skips cleanly if nothing changed** — so it is always safe to call even if this run wrote nothing to the stronghold.
- Do NOT open a PR or feature branch for this — the stronghold pushes direct to `main`. (This syncs only the stronghold; any sub-repo code changes keep their own normal git-commit/git-push/PR flow.)

## Next-Command Handoff (final output — always the last thing printed, after the sync)

Print this block from values resolved this run (no extra tool calls):

```
Next step — plan written. Copy/paste:

  /plan-reviewer <repo>/<plan-name>

Repo: <repo-dir> · Branch: <plan-branch> · Worktree: <worktree-or-primary-clone> · Plan: ~/code/plans/<repo>/open/<topic>/<plan-name>.md
Tip: /clear first — all state lives in the plan file, so a fresh context loses nothing and cuts token spend.
```

`<repo>` is the `Plans bucket` value, `<repo-dir>` the working repo's directory, `<plan-name>` the plan basename without extension, `<plan-branch>` the plan's `branch:` frontmatter (print `none` when Step 3 wrote none), `<worktree-or-primary-clone>` the plan's `worktree:` value expanded to `~/code/<value>` (or the primary clone `~/code/<repo-dir>`; `~/code` for `stronghold` plans). A sub-plan created via Step 0 gets the same block for the sub-plan file.
