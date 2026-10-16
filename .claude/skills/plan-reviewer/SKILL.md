---
name: plan-reviewer
description: Review a plan with a scope-scaled panel (2, 4 or 5 parallel subagents) over up to 2-3 passes - mechanical fixes auto-applied, design decisions asked via AskUserQuestion, early exit on a clean pass. Writes <plan-dir>/reviews/<name>-review.md. Argument is the plan name or <repo>/<plan-name> (e.g. "/plan-reviewer app-main/auth-redesign").
argument-hint: Plan-name [scope]
---

# Plan Review with Parallel Subagents (Scope-Scaled, Up to 3 Passes)

Review a plan using a panel of specialized subagents running in parallel — 2, 4, or 5 depending on the plan's size/risk tier (Step 1e) — then merge their findings into a single review document. Automatically loops up to the tier's pass cap (2 for `small`, 3 for `medium`/`large`), pausing between each pass for the user to answer design decisions via interactive `AskUserQuestion` prompts.

## Resolving repo-specific values (read the seam first)

This skill is repo-agnostic. Every repo-specific value comes from the **working repo's `## Claude Config`** section (its `CLAUDE.md`). Read that section once at the start and resolve these seam keys:

- **Plans store (central)** — plans live in the central stronghold store at `~/code/plans/<repo>/`, resolved by `<repo>` (the value of the working repo's `Plans bucket` seam key; `stronghold` for `~/code` itself; `<repo-dir>`, the working repo's directory name, is what script calls take). Reviews, research, and transient tmp all live BESIDE the plan under its resolved `<plan-dir>`: review documents are written to `<plan-dir>/reviews/` and named `<plan-name>-review.md`; `<plan-dir>/tmp/` holds transient subagent I/O; `<plan-dir>/research/` holds plan-creator's persisted research. See Step 1 for how `<repo>` and `<plan-dir>` are resolved.
- **Default branch** — the protected branch for the Branch Guard (fall back to `main`/`master` if unset).
- **Stack** — the hyphenated stack-tag set that gates the opt-in stack reference files (see "Stack protocols" below).
- **Commands** — the repo's test/build command table (Integration tests / UI tests / JS-unit tests / Build / Lint-format), used to phrase verification-layer checks in stack-neutral terms.

If a key is absent (seam not fully rolled out), degrade gracefully: fall back to the ambient value and note the assumption rather than erroring.

## Stack protocols (opt-in)

Several reviewer heuristics are stack-bound. Read the working repo's `## Claude Config` → **Stack**. For each `references/stack/<tag>.md` whose declared tags intersect the repo's Stack, that file's stack-specific checks apply for the areas it covers; the per-area SA reference files point reviewers to it. If the repo declares no Stack or none match, reviewers apply only the stack-neutral checklists inline in the SA reference files. The illustrative code examples that remain inline in the SA files (Python class bodies, jQuery handlers, etc.) are labeled as one stack's example, not universal rules.

## Branch Guard

**Check the WORKING SUB-REPO's branch, never the stronghold cwd.** The cwd (`$CLAUDE_PROJECT_DIR`) is always `~/code` (the stronghold), which sits on `main` *by design* — a bare `git branch --show-current` reads the stronghold and would false-fire this guard on every plan review. The plan targets a sub-repo (its `repo:` frontmatter, which is the `Plans bucket` value → `<repo>`, resolved the same way as Step 1), and under Worktree Discipline its branch usually lives in a worktree, not the primary clone. Resolve `<repo>` and `<worktree>` first with `~/code/.claude/scripts/wt-resolve.sh <repo-dir> --plan <plan-file> [--assert]` (Step 1 defines the resolve; you may briefly peek at the plan's `repo:`, `branch:` and `worktree:` frontmatter before Step 1's full read to do so). Consequences: `kind=none` means the branch is checked out nowhere, and this skill does NOT run `make wt-new` (the worktree is created at execution time), so use the primary clone; an `--assert` mismatch means STOP and report; `has no worktree support` also means the primary clone. Then read the branch from the resolved checkout:

```
git -C <worktree> branch --show-current    # e.g. ~/code/app-main, or ~/code/app-main/.claude/worktrees/<slug>
```

(Use bare `git ...` instead when `<worktree>` is already the cwd (e.g. `~/code` for stronghold plans) — a redundant `-C` is blocked by a hook. If the sub-repo dir doesn't exist, or the plan bucket is `stronghold` itself, the working repo *is* `~/code`.)

Then, using that checkout's branch. This skill never runs `git checkout`/`git switch` and never creates a branch or worktree — a plan review is read-only for source:
1. If it is on the repo's **Default branch** (from its `## Claude Config`; treat both `main` and `master` as protected when unset) while the plan records a different non-default `branch:` for which no worktree or branch was found (Step 1's resolve fell back to the primary): say in one line that the review reads source from the Default branch (the plan's feature branch/worktree is created at execution time via CLAUDE.md → Worktree Discipline), then proceed. Reviewing a plan does not need the feature branch.
2. If it is on a feature branch (or the plan targets the Default branch itself): proceed normally.

## Review Loop (Up to `<pass-cap>` Passes)

The review runs as a loop with up to `<pass-cap>` passes (Step 1e: 2 for `small`, 3 for `medium`/`large`). Each pass executes Steps 2-8 below. Between passes, the user answers design decisions via `AskUserQuestion`. The loop exits early if:

- **A pass produces 0 critical + 0 major findings** → plan is clean, stop reviewing
- **Pass `<pass-cap>` completes** → hard cap reached; any remaining issues are tagged "resolve during implementation" in the review document

After the final pass (whether early exit or hard cap), proceed to Step 9 (post-loop coherence check), then Steps 10-11 (root cause analysis and skill improvement) if prior reviews exist.

### Pass tracking

Track the current pass number (1 through `<pass-cap>`). When writing to the review document:
- Pass 1: `## Review — <YYYY-MM-DD>`
- Pass 2+: `## Review — <YYYY-MM-DD> (Pass N)`

## Workflow

### Step 1: Locate the Plan (once, before the loop)

- Read the **working repo's `## Claude Config`** and resolve `<repo>` = the value of the working repo's `Plans bucket` seam key (a missing key means STOP and report it; plans about `~/code` itself use the literal bucket `stronghold`). `<repo-dir>` = the working repo's directory name (the basename of its primary clone under `~/code`): it is what is passed to `wt-resolve.sh` and `plan-folder.sh` and used in `~/code/<repo-dir>` paths. Plans live in the **central store** at `~/code/plans/<repo>/`, never inside the sub-repo.
- **Argument forms:** `<plan-name>` or `<repo>/<plan-name>`. A `<repo>/` prefix (a `Plans bucket` value, e.g. `app-main/soft-delete-trash-api`) fixes the working repo regardless of session context: it is the `~/code/<dir>` whose `Plans bucket` equals the prefix (identical to the directory name in every current repo), or `~/code` for `stronghold`; match only the part after the slash as **$0** below.
- Search `~/code/plans/<repo>/open/**/` recursively for a file matching **$0** (fuzzy match on filename). If not found there, search `~/code/plans/<repo>/completed/**/`.
- Do NOT read the full plan in this (coordinator) context; the reviewer subagents read it in full and every coordinator turn would re-read it. Get the outline with `~/code/.claude/scripts/plan-status.sh <repo>/<plan-name>` (path, frontmatter, `step_count`, step line ranges, sections), then take the Step 1e signals with count-only greps on the plan file (one `grep -c -i -E '<pattern>' <plan-file>` each, see Step 1e). `Read` only a short range when a detail is genuinely needed (e.g. the first ~40 lines for Step 1c). Fall back to reading the whole plan when `plan-status.sh` reports no `### N.` step headings.
- **Resolve `<worktree>` once, beside `<repo>`.** Run `~/code/.claude/scripts/wt-resolve.sh <repo-dir> --plan <plan-file>` (CLAUDE.md → Worktree Discipline part (1); the script header documents the rules). If the plan bucket is `stronghold` (pass the literal `stronghold` as the script's repo argument), `<worktree>` = `~/code` (use bare `git ...`, never `-C`, when `<worktree>` is already the cwd — a redundant `-C` is blocked by a hook). Otherwise `<worktree>` is the script's `worktree_abs`: a verified `worktree:`, else the worktree holding `branch:`, else (`kind=primary`, `kind=none`, no `branch:`, `branch:` = the Default branch, or no worktree support) the primary clone `~/code/<repo-dir>`. Store `<worktree>` for the Branch Guard and Step 2. Never create a worktree here.
- Derive `<topic>` from the plan file's parent directory (e.g., if the plan is at `~/code/plans/<repo>/open/auth/my-plan.md`, `<topic>` is `auth`). Store `<plan-dir>` = the plan's resolved directory (`~/code/plans/<repo>/open/<topic>/` if found under `open/`, else `~/code/plans/<repo>/completed/<topic>/`). Every `<plan-dir>/...` path below (`tmp/`, `research/`, `reviews/`, the plan file itself) resolves under this one directory.

### Step 1b: Prepare tmp directory

Before launching subagents, create `<plan-dir>/tmp/` if it does not already exist.

### Step 1c: Detect Master Plan (if any)

Some plans are sub-plans of a multi-PR master plan — the master encodes cross-phase contracts that reviewers cannot reconstruct from the sub-plan alone (e.g., "Phase 3 typed a shared field as unknown[] specifically for Phase 6 to narrow"). Detect the master with a two-tier check:

1. **Primary signal (explicit link)** — Read the first ~40 lines of the plan. If a `**Master plan:**` field exists, extract its path.
2. **Fallback signal (auto-discovery)** — Glob in this tier order (same order as `/plan-creator` Step 0): `~/code/plans/<repo>/open/*/*-master.md`; then, only when `<repo>` != `stronghold`, `~/code/plans/stronghold/open/*/*-master.md` (a cross-repo master); then `~/code/plans/<repo>/completed/*/*-master.md`. For each match, grep for either:
   - The current plan's filename (without extension), OR
   - The plan's own recorded `branch:` frontmatter value (already read in Step 1 — an exact, git-free source, since a branch checked out in a worktree is never the primary clone's current branch). If `branch:` is absent (older plan), match on the plan filename only.
   Take the first tier that contains a master referencing the current plan/branch, not merely the first non-empty tier (an unrelated open master must not hide a completed or stronghold master that does reference this plan). If several masters in that tier match, use the one whose body links this plan; if none clearly does, treat it as no master (null).

If a master plan is found, store `<master-plan-path>` for Step 2. If no master exists, record `<master-plan-path>` as null and proceed.

If the plan appears to be a sub-plan (lives in a folder referenced by `**Sub-plan:**` from any master) but no master plan is detected by either signal, flag this to the user as a skill setup warning — the reviewers will miss cross-phase context.

### Step 1d: Detect Scope Directive (optional)

Some invocations target a narrow re-review (e.g., "only re-review the metrics dimension changes, skip the auth flow") rather than a full re-evaluation of the plan. Detect this once, before Pass 1:

1. Step 1's plan lookup fuzzy-matches **$0** against plan filenames. If the invocation's raw arguments contain additional free text beyond what matched the plan filename, treat that remaining text verbatim as `<scope>`.
2. If no extra text is present, `<scope>` is null and every pass reviews the full plan (current behavior — unchanged).
3. If `<scope>` is set, it applies to **every pass** of this invocation (Step 2's reviewer subagents only — see Step 2's scope-conditional line). It is fixed for the whole invocation: it is not re-derived per pass and is not narrowed or widened by Pass 2+ prior-fix data or DD resolutions.

Store `<scope>` (or null) for use in Step 2. It does not affect the Pass 2+ prior-fix check (Step 2a, run inside the coordinator), which always re-checks every prior fix regardless of scope.

### Step 1e: Classify Review Scope

Using the `plan-status.sh` outline and count-only greps from Step 1 (no full plan read, no extra subagent call), classify the plan once, before Pass 1, into `small` / `medium` / `large`. This is fixed for the whole invocation — it is not re-derived per pass. Fail toward the larger panel: for each `touches_*` signal below, any grep hit (count > 0) for its pattern counts as true, and a signal you cannot compute counts as true. Suggested patterns: `touches_ui` = `mock|\.html|template|frontend|css|\.tsx?\b|\.jsx?\b|aria|button|modal|layout`; `touches_endpoints` = `route|endpoint|handler|api/|request|response`; `touches_data_model` = `schema|migration|model|column|table|orm|database`.

Compute these signals:
- `step_count` — number of `### N.` top-level headings under `## Steps` (the `steps` count in the `plan-status.sh` `progress:` line)
- `is_sub_plan` — a master plan was detected in Step 1c
- `touches_ui` — the same signal already used to gate Subagent #7 (UX/Accessibility) (plan has a Mockup link / UI Mockup Protocol section / frontend-visible changes)
- `touches_endpoints` — plan mentions route/endpoint/handler changes (route decorators, "endpoint", "API route", request/response chain)
- `touches_data_model` — plan mentions data-model changes (request/response schemas, ORM models, or DB migrations)

Classify:

| Tier | Condition | Panel | Pass cap |
|---|---|---|---|
| **Large** | `is_sub_plan` OR `step_count >= 9` | Full 5 (4 if `!touches_ui`) — current default | 3 (unchanged) |
| **Small** | `step_count <= 3` AND none of `touches_ui`/`touches_endpoints`/`touches_data_model` | 2 agents: #1 Correctness & Integration, #5 Verification | 2 |
| **Medium** | everything else (the common case) | 4 (5 if `touches_ui`) — current default | 3 (unchanged) |

Store `<review-tier>` and `<pass-cap>` for use in Step 2 (panel selection) and Step 8b (hard-cap check). Mention the tier and panel size in the Step 8a pass summary so the user can see why a plan got a lighter or heavier review.

### Step 2: Launch Parallel Review Subagents

Launch the subagents selected by `<review-tier>` (Step 1e) **in parallel** using the Agent tool with **minimal prompts**. Each subagent reads its own instructions from individual reference files — the orchestrator does NOT inline the full checklist or response format.

**Subagent prompt template** (send this to each, filling in the blanks):

> You are Subagent #N (<Role Name>) reviewing the plan at `<plan-dir>/<plan-name>.md`.
>
> 1. Read `.claude/skills/plan-reviewer/references/common-response-format.md` for the response format and rules, then read `.claude/skills/plan-reviewer/references/<sa-file>` for your specific review checklist. Follow those instructions exactly.
> 2. <If master plan detected:> Read the master plan at `<master-plan-path>` FIRST to understand cross-phase context — which phases precede/follow this one, and what shared invariants (types, contracts, data shapes) are staged across phases. Every finding about a "type that's too wide" or "shared module change" must be evaluated against the master's staging plan before being classified as a regression vs. intentional.
> 3. Read the plan in full. Then, **before independently discovering source files**, check `<plan-dir>/research/` for the research file(s) mapped to your role (see table below via `Glob(pattern: "<plan-dir>/research/research-*.md")`). If present, read them first — they already name the relevant files, signatures, and patterns from plan-creator's research phase, so you can skip the broad Glob/Grep discovery sweep. You must still open and `Read` each source file you cite as evidence before asserting a finding (research may be incomplete, stale, or the codebase may have changed since) — the research file saves the *discovery* step, not the verification step. If no research files exist (plan predates this workflow, or was hand-written), fall back to independent discovery exactly as before.
> 3b. When opening or citing sub-repo source files, read sub-repo source from `<worktree>` (the checkout resolved in Step 1: the plan's worktree, else `~/code/<repo-dir>`, or `~/code` for stronghold plans) by absolute path, never from a different checkout. Do not run `git checkout`, `git switch`, or create a branch or worktree.
> 4. Write your complete JSON response to `<plan-dir>/tmp/<role>.md` **using the `Write` tool** — NEVER `cat <<EOF`, `python3 << 'EOF'`, `cat >`, `tee`, `printf >`, `echo >`, or any Bash heredoc/redirect (any heredoc or inline script with `{` + quotes trips the brace+quote security prompt).
> 5. Return only: `Written to <path>`.
>
> **Do not defer any dimension to a follow-up pass.** Every checklist item must be evaluated.
> <Pass 2+ only: "This is Pass N. Do not re-flag issues already fixed. Prior fixes: [concise bullet list of fix titles from earlier passes]">
> <If scope present (Step 1d): "This review is SCOPED to: <scope>. Constrain your SOURCE FILE reads to files/areas within this scope — you must still read the full plan file (step 3 above) for structural understanding, but do not read or flag source files outside the scope. If your checklist dimension has nothing in-scope to evaluate, return verdict PASS with an empty findings array; do not flag out-of-scope areas.">

**Key rules:**
- Each subagent reads source files independently — the main agent does NOT pre-read files
- All subagent launches must be in a **single message** for true parallelism
- Use `model: sonnet` for all review subagents
- Only launch the subagents selected by `<review-tier>` (Step 1e) — for `small`, this means #2, #3, and #7 are simply not launched this pass, not launched-then-discarded
- Skip Subagent #7 (UX, Accessibility & Edge Cases) if the plan has no UI/frontend changes — it adds no value for backend-only plans (this is in addition to, not a substitute for, the tier-based skip)
- NEVER use `subagent_type: "Explore"` — Explore agents cannot use the Write tool. Omit `subagent_type` (defaults to general-purpose)
- If a `<scope>` directive is set (Step 1d), it constrains subagents' SOURCE FILE reads only — every subagent still reads the full plan file and evaluates every checklist item, returning PASS + empty findings where nothing in-scope applies

Subagents and the research file(s) each should check first (Step 3 of the prompt template above). #1 and #3 each cover two former roles, merged for token efficiency — the pairs shared both their source files and a "what's missing" framing (see each file's Part A / Part B split):

| # | Name | SA reference file | Output filename | Research file(s) to check first |
|---|---|---|---|---|
| 1 | Correctness, Accuracy & Codebase Integration | `sa1-correctness.md` | `correctness.md` | `research-architecture.md`, `research-schemas.md` |
| 2 | Full-Stack Trace | `sa2-full-stack-trace.md` | `full-stack-trace.md` | `research-request-chain.md`, `research-dependencies.md` |
| 3 | Ordering, Dependencies, Cleanup & Completeness/Risk | `sa3-ordering.md` | `ordering.md` | `research-dependencies.md`, `research-schemas.md` |
| 5 | Verification & Test Coverage | `sa5-verification.md` | `verification.md` | `research-tests.md` |
| 7 | UX, Accessibility & Edge Cases | `sa7-ux-accessibility.md` | `ux-accessibility.md` | `research-ux-interaction.md` |

Only the subagents selected by `<review-tier>` launch — for `small`, that's rows 1 and 5.

### Step 2a: Prior-Fix Check (Pass 2+ only — runs inside the coordinator)

There is no separate Prior-Fix Verifier subagent. On every pass after Pass 1 (up to `<pass-cap>`), the Step 3b coordinator re-verifies every applied mechanical fix and resolved DD from the fix manifest (`<plan-dir>/tmp/fix-manifest.md`) against the current plan text, using the procedure in `.claude/skills/plan-reviewer/references/coordinator.md` ("On Pass 2+: Prior-fix verification"), and merges any regression into its findings as a critical mechanical finding with `sources: ["Prior-Fix Verifier"]`. Do not launch a verifier alongside the reviewer panel.

### Step 3: Collect and Validate Results

After all subagents return their one-line confirmations, verify each expected `<plan-dir>/tmp/<role>.md` file exists — only for the roles actually launched per `<review-tier>` (Step 1e) and the UI skip. E.g. for `small`, only `correctness.md` and `verification.md` are expected; for `medium`/`large` it's the full set: `correctness.md`, `full-stack-trace.md`, `ordering.md`, `verification.md`, and `ux-accessibility.md` (if launched).

**Use the Glob tool** (`Glob(pattern: "<plan-dir>/tmp/*.md")`) to check for the files — **never use Bash `ls` with brace expansion** (`{a,b,c}`) as it triggers sandbox security prompts.

For each file:
- If a file is missing or contains invalid JSON, note it — the coordinator (Step 3b) will treat that reviewer as FAIL with a parse error finding

### Step 3b: Launch Coordinator Subagent

After confirming all expected files (per `<review-tier>`) are present, launch a **single coordinator subagent** to deduplicate, conflict-detect, and produce the output files.

The coordinator subagent prompt:

> Read `.claude/skills/plan-reviewer/references/coordinator.md` for the full coordinator instructions. The reviewer files launched this pass are at `<plan-dir>/tmp/`: `<list the actual filenames launched this pass, e.g. correctness.md, verification.md for a small-tier pass>`. Follow the coordinator workflow, then write:
>
> 1. **`<plan-dir>/tmp/coordinator.md`** — the full JSON output (verdicts, summaries, files_read, findings) as specified in the reference file.
> 2. **`<plan-dir>/tmp/coordinator-summary.md`** — a short summary for the orchestrator containing ONLY:
>    - `verdicts`: one-line per reviewer (`correctness: PASS/FAIL`, etc.)
>    - `counts`: `{critical: N, major: N, minor: N}` (deduplicated totals)
>    - `mechanical_count`: number of findings with `fix_type: "mechanical"` (computed AFTER merging any Pass 2+ prior-fix regressions — see below)
>    - `design_decision_count`: number of findings with `fix_type: "design_decision"`
>    - `design_decision_titles`: list of DD finding titles (one line each) — the orchestrator needs these for AskUserQuestion prompts
>    - `design_decision_options`: **REQUIRED** — map of DD title → array of option strings (e.g., `{"DD title 1": ["Option A: description", "Option B: description"]}`). The orchestrator uses this to present DDs without reading the full coordinator file.
> 3. **`<plan-dir>/tmp/fix-batch-1.md`, `fix-batch-2.md`, ...** — the mechanical-fix_type findings ONLY, pre-split into fixed-size batches of **6**, in the same order they appear in `coordinator.md`'s `findings` array. Batch count = `ceil(mechanical_count / 6)`. Every mechanical finding — including any merged from `prior-fix-regressions.md` — appears in exactly one batch file; the last batch may have fewer than 6. Each batch file:
>    ```json
>    { "batch": N, "findings": [ { "index": <1-based index across ALL mechanical findings>, "title": "...", "step": "Step N", "file": "path/to/file (if applicable)", "fix_description": "...", "evidence": "... (if present on the finding)" } ] }
>    ```
>
> **On Pass 2+ (this is Pass N, N ≥ 2):** Also perform the prior-fix verification described in `coordinator.md` ("On Pass 2+: Prior-fix verification") using `<plan-dir>/tmp/fix-manifest.md` and the current plan `<plan-dir>/<plan-name>.md`. Merge each regression into the `findings` array as a critical finding with `sources: ["Prior-Fix Verifier"]` and `fix_type: "mechanical"` **before** computing `mechanical_count` and writing the fix-batch files, so regressions get their own batch slot. On Pass 1 skip this entirely. Also state in your return line how many prior fixes you re-verified and how many regressed.
>
> Return only: `Written to <plan-dir>/tmp/coordinator.md, coordinator-summary.md, and N fix-batch file(s)` (state the actual N).

- Uses `model: sonnet` for speed
- The orchestrator reads ONLY `coordinator-summary.md` — never `coordinator.md` directly. The fixing subagents (Step 4) read only their assigned `fix-batch-N.md`; the full `coordinator.md` is consumed only by the writer subagent (Step 7).

### Step 4: Auto-Apply Mechanical Fixes

Read `<plan-dir>/tmp/coordinator-summary.md` to get `mechanical_count`. If `mechanical_count` is 0, skip this step entirely (no fixer subagents to launch). Otherwise compute `batch_count = ceil(mechanical_count / 6)` — this is exactly the number of `fix-batch-N.md` files the coordinator (Step 3b) wrote.

Before launching, use **`Glob(pattern: "<plan-dir>/tmp/fix-batch-*.md")`** to confirm the file count matches `batch_count`. If it doesn't, treat this as a coordinator error: fall back to having one fixer subagent read `coordinator.md` directly for this pass only, and note the discrepancy in the Step 8a pass summary.

Launch **one fixing subagent per batch file** (`batch_count` subagents total, all in a single message for true parallelism), each with `model: sonnet` (these apply already-specified plan edits; the next pass re-reviews them). Each fixing subagent reads ONLY its own `<plan-dir>/tmp/fix-batch-<N>.md` — never `coordinator.md`. Each fixing subagent:

1. Reads `<plan-dir>/tmp/fix-batch-<N>.md` (its assigned findings — already scoped, no self-filtering needed)
2. Reads the plan file
3. Applies each mechanical fix (edits to plan text only — never source files)
4. **After each edit, re-reads the finding's `fix_description` and `evidence` and re-verifies the edit against them — not just that surrounding text reads coherently.** Specifically:
   - Confirm the edit says what `fix_description` asked for, word-for-word where the fix specifies exact text — not an approximation.
   - **Execution-order check (required whenever the fix references a name defined elsewhere in the same class/module/function scope):** trace the order the runtime actually evaluates the lines — do not assume textual presence in the file equals correct evaluation order. (One stack's example: Python class bodies execute top-to-bottom at class-definition time, so a class attribute referenced inside `__table_args__` — or any other class-body expression — is undefined if it appears *after* that expression in source, even though the file "contains" it.) A fix that reintroduces this ordering violation is not correctly applied — fix it now, in this same pass, rather than letting a later pass's reviewer panel rediscover it.
   - If the self-check finds the edit is wrong or incomplete, redo it before returning — do not report it as applied and rely on the next pass to catch it.
5. Returns a JSON summary of applied/skipped fixes (include each finding's `title` and `index`, plus a `self_corrected: true/false` flag per finding — set `true` if step 4's re-check required a redo)

**All fixing subagents launch in a single message** for true parallelism. After all return, collect results into `applied` and `skipped` lists.

**Skipped items**: If a fixing subagent discovers that its "mechanical" fix actually requires a design choice, it MUST skip it and explain why. Skipped items are promoted to design decisions.

**The orchestrator NEVER reads `coordinator.md` or any `fix-batch-N.md` directly.** These are consumed only by the fixing subagents. The writer subagent (Step 7) separately reads the full `coordinator.md`.

**Do NOT apply any changes to source files — only the plan document.**

### Step 5: Present Design Decisions via AskUserQuestion

The orchestrator has the DD titles and options from `coordinator-summary.md` (`design_decision_titles` and `design_decision_options` — both REQUIRED fields). Use these to construct AskUserQuestion prompts without reading the full coordinator file.

Present each design decision using `AskUserQuestion`:

**Constraints:**
- Max 4 questions per call, each with max 4 options
- Batch across multiple calls if needed
- `multiSelect: false` (DDs are mutually exclusive)

**Mapping:**
- `question`: `"DD-N: [Step M] <finding title>?"` — include enough context for the user to decide
- `header`: `"DD-N"` (max 12 chars)
- `options`: label (1-5 words) + description (trade-off) + optional `preview` (code snippet)

**Do NOT apply design decisions to the plan without explicit user confirmation.**

### Step 6: Apply User's Design Decisions via Subagent

**IMPORTANT:** Launch a single subagent (`model: sonnet`) to handle all DD resolution work. The main orchestrator agent must NOT read/edit plan files or review files directly for DD application — this pollutes the main context window with large file contents. Instead, the subagent receives:
- The plan file path and review file path
- All user answers (DD number → chosen option text and any user notes)
- Instructions to: (1) apply each chosen option to the plan, (2) update the `**Chosen:**` field in the review document, (3) if the user selected "Other" with custom text, apply that instead

**Fragility-pattern sweep (required for every DD that resolves a class of fragility):** When a DD resolves a fragility pattern — a readiness race (flat sleep vs. poll loop), an unquoted variable, a missing error guard, a stale assertion — the DD-application subagent MUST scan the same file/code-block area for all other occurrences of that identical fragility before marking the DD resolved. If sibling occurrences exist, apply the same fix to all of them in the same step and list each sibling in the DD resolution summary. Never mark a DD complete if the same fragility remains in a sibling block of the same file.

**Grounding Rule — source-function verification (required for every DD that creates a new plan step describing modifications to an existing source function):** When a DD resolution adds a new step (or new sub-bullets within a step) that instructs 'replace X with Y' or 'remove X' in a named source function, the DD-application subagent MUST read that function before writing the step text. Do NOT describe the function's current contents from plan prose or DD option text alone — verify the described content actually exists in the function. If the described content (e.g., a DOM selector to replace) does not exist, rewrite the step as a net-new addition rather than a replacement instruction. A step that says 'replace X' when X does not exist will mislead the implementer.

**Grounding Rule — use-site trace (required for every DD that introduces or widens a variable used in a guard or branch):** When a DD's chosen option introduces a new variable, widens a variable's type, or changes a condition (e.g., an equality check against a new literal value, a `!== null` guard), the DD-application subagent MUST trace that variable to every write site in the same code area and verify: (a) the write site actually assigns the gating value (e.g., if the gate is `x === 'flows'`, some code path must write `x = 'flows'`), and (b) the type width at the write site is not narrower than the gate requires. If no write site assigns the gating value, the DD resolution is internally inconsistent — flag it as a critical regression before marking the DD resolved. (This catches the dead-code-gate class: a variable widened in type but never assigned the gating value at its write site.)

**Grounding Rule — import name-collision guard (required for every DD that adds an import into an existing shared file):** Before writing plan step text that adds an import of a bare symbol S into a shared file (a shared test-fixture/config module, a shared utils module, a shared init/index file), the DD-application subagent MUST run SA#1's import name-collision check (Part B, Codebase Integration) against that file's import block. If S is already bound from a different module, the chosen option MUST alias it and update all call sites of the newly added symbol. Never write a bare-name import without completing the collision check — in languages where imports bind at module/file scope (e.g. Python), a bare re-import silently rebinds the name and the shadowed call sites fail at runtime with a wrong-signature error no linter catches.

**Grounding Rule — execution-order self-check (required for every DD that references a name defined elsewhere in the same class/module/function scope):** Trace the order the runtime actually evaluates the lines — textual presence in the file is not the same as correct evaluation order. (One stack's example: Python class bodies execute top-to-bottom at class-definition time, so a class attribute referenced inside `__table_args__` — or any other class-body expression — is undefined if the chosen option places it *after* that expression in source.) Verify the actual order before marking the DD resolved.

**Required final self-check (before returning, for every DD applied this pass):** Re-read the specific plan text you just wrote for each DD and diff it against the `chosen` option's exact wording — not a paraphrase. A DD marked resolved with text that drifted from what the user actually chose (wrong selector, wrong event order, wrong flag value) is the single most common cause of a finding reappearing in the next pass — catch it here, in this same subagent call, rather than letting the next pass's prior-fix check (Step 2a) or a full next-pass reviewer panel rediscover it a pass later. Redo any DD that fails this check before returning. Include a `self_corrected: true/false` flag per DD in the summary.

After applying all DD resolutions, flag to the orchestrator any newly written step text (entirely new steps, not merely edited text). The orchestrator should brief Pass 2+ subagents: 'The following steps were entirely new as of the prior pass's DD resolution — treat them as first-pass steps for their content even if their surrounding context was reviewed before.' This ensures DD-authored steps get the same scrutiny as original plan steps.

The subagent reads the files, makes all edits, and returns a summary of what was changed. The orchestrator only needs the summary to continue.

### Step 7: Write Review Document via Subagent

**The orchestrator does NOT read `coordinator.md` or write the review document directly.** Instead, launch a **writer subagent** that handles all review document construction. By this point, all mechanical fixes have been applied (Step 4) and all DDs have been resolved (Steps 5-6) — the writer has the full picture.

The writer subagent prompt:

> You are a review document writer. Read `<plan-dir>/tmp/coordinator.md` (the full coordinator output) and write the review section into the review file for this plan. The reviews location is `<plan-dir>/reviews/` (beside the plan in the central store); the review file is `<plan-name>-review.md` inside that directory.
>
> Create the reviews location directory if it doesn't exist. If the review file already exists, **append** a new dated section — do not overwrite.
>
> **Pass info:** This is Pass N. Date: <YYYY-MM-DD>. Use heading `## Review — <YYYY-MM-DD>` (Pass 1) or `## Review — <YYYY-MM-DD> (Pass N)` (Pass 2+).
>
> **Applied mechanical fixes:** The following fixes were already applied to the plan before you ran: [list from Step 4 applied results]. Mark each corresponding item as `[x] _(applied)_` when writing the Mechanical Fixes checklist.
>
> **Resolved DDs:** The following DD choices were applied before you ran: [list from Step 6 summary]. Fill in the `**Chosen:**` field for each.
>
> **Prior-fix check (Pass 2+ only):** The coordinator re-verified [N] prior fixes and found [M] regressions (from its return line; any regression is already a critical finding in `coordinator.md`). State this in the Summary prose.
>
> **Update the fix manifest:** After writing the review document, also update `<plan-dir>/tmp/fix-manifest.md`. If it doesn't exist yet (Pass 1), create it with `{"passes": []}` first, then read it (it's small), and append a new entry to `passes` for this pass:
> ```json
> { "pass": N, "applied_mechanical_fixes": [{ "title": "...", "root_cause": "..." }], "resolved_dds": [{ "id": "DD-N", "title": "...", "chosen": "..." }], "unresolved": [{ "title": "...", "reason": "..." }] }
> ```
> This is the same applied/resolved/unresolved data you're already rendering into the review document — it costs nothing extra to compute. Write the full updated manifest with the Write tool.
>
> **Document structure:** Use `classification` and `sources` from each finding for attribution: `duplicate` → append `*(flagged by: Subagent A, Subagent B)*`; `conflict` → render as a design decision naming disagreeing subagents.
>
> **Coverage checklist mapping:** Mark `[x]` if the primary subagent for that area reported reading relevant files (check `files_read`). If the primary subagent for an area was not launched this pass (Step 1e tier skip, e.g. `small` tier omits #2/#3/#7), mark that row `— (not launched, <tier> tier)` instead of leaving it blank or `[x]`.
> - Imports → #3 Ordering | Type annotations → #1 Correctness | Error handling → #2 Full-Stack Trace
> - Test coverage → #5 Verification | Breaking changes → #3 Completeness/Risk | Config → #1 Integration | Naming → #1 Integration
> - Accessibility → #7 UX/Accessibility | Cross-feature interactions → #7 UX/Accessibility | Empty states → #7 UX/Accessibility
>
> **DD numbering:** Design decisions use sequential IDs continuing from prior passes. Read the existing file to find the highest DD-N, then start from DD-(N+1). If no prior DDs exist, start at DD-1.
>
> **`### Resolve During Implementation` section:** Include ONLY items that are genuinely unresolved after Steps 4-6 (i.e., skipped mechanicals that could not be applied, or DDs where no option was chosen). If all mechanicals and DDs were resolved, omit this section entirely or write "None."
>
> **Count integrity (required):** Immediately before writing the Summary prose and the Subagent Results table, build the final deduplicated findings array you are about to render (after merging any Pass 2+ prior-fix regressions) and count it directly by severity. Every count you state anywhere in the document (Summary prose, e.g. "N criticals, N majors"; the Subagent Results table's per-reviewer finding counts; any heading counts in the Findings section) MUST equal the actual number of items enumerated below it — never a number recalled from earlier reasoning or estimated. If you write "twelve majors" in prose, there must be exactly 12 items under the Major heading. Re-count after any last-minute change to the findings list before finalizing prose.
>
> Write the review section using this structure:
> ```
> ### Summary — 2-3 sentences
> ### Subagent Results — table with verdicts and per-reviewer finding counts (counted from the findings array — see Count Integrity rule above)
> ### Findings — grouped by Critical/Major/Minor (omit empty categories)
> ### Verification Gaps — steps lacking verification
> ### To-Do: Mechanical Fixes — checklist ([x] _(applied)_ for applied; [ ] with reason for skipped)
> ### Design Decisions — each with DD-N, context, options table, filled Chosen field
> ### Verdict — one of: Ready / Proceed after minor / Requires changes
> ### Coverage Checklist — 7-area table
> ```
>
> Return only: `Written to <review-file-path> and <plan-dir>/tmp/fix-manifest.md`

- Uses `model: sonnet` for speed
- The orchestrator reads ONLY `coordinator-summary.md` (from Step 3b) — never the full coordinator output or the review file

### Step 8: Report and Loop Decision

#### 8a: Present results to user

Present a concise summary using data from `coordinator-summary.md` and fix subagent results:
- Current pass number (e.g., "Pass 1 of `<pass-cap>`") and `<review-tier>` (Step 1e)
- Per-reviewer verdicts
- Count of critical / major / minor findings
- **Mechanical fixes applied** (count)
- **Mechanical fixes skipped** (if any, with reasons)
- **DD choices applied** (count)
- **Self-corrections** (count of `self_corrected: true` across Step 4 + Step 6 results, if any) — a nonzero count means a fix or DD would have drifted into next pass's findings without this pass's self-check catching it; worth a glance if the number is consistently high across plans, as it signals the self-check itself may need sharpening
- Path to the review file

#### 8b: Loop decision

After the writer completes, evaluate:

1. **Early exit check**: Did this pass produce 0 critical + 0 major findings?
   - **Yes** → Skip remaining passes. Print: "Pass N clean (0 critical, 0 major). Plan is ready for implementation." (the exact `/run-plan` command is printed once, in the final Handoff block). Proceed to Step 9. (`<plan-dir>/tmp/` is NOT deleted yet — Step 9 needs `fix-manifest.md`; deletion happens at the end of Step 9.)
   - **No** → Continue to next pass.

2. **Hard cap check**: Is this pass number equal to `<pass-cap>` (Step 1e — 2 for `small`, 3 for `medium`/`large`)?
   - **Yes** → Stop reviewing. Print: "Pass N complete (hard cap for <review-tier> tier). Remaining issues tagged for implementation." (the exact `/run-plan` command is printed once, in the final Handoff block). Proceed to Step 9. (Same deferred deletion as above.)
   - **No** → Print: "Pass N complete. Starting Pass N+1..." and loop back to **Step 2**.

### Step 9: Post-Loop Coherence Check

After the loop exits (early exit OR `<pass-cap>` hard cap), launch one final lightweight subagent to verify the review document is internally consistent with the plan. `<plan-dir>/tmp/` is still present at this point — Step 8b intentionally deferred its deletion until after this step.

Prompt template:

> You are the Post-Loop Coherence Verifier.
>
> 1. Read the fix manifest at `<plan-dir>/tmp/fix-manifest.md`. Collect every `resolved_dds` entry (id, title, chosen) across ALL entries in `passes`, and the `unresolved` array from ONLY the LAST entry in `passes` (earlier passes' unresolved items are superseded and not authoritative). Do NOT read the full review file.
> 2. For every resolved DD collected in step 1, identify what the chosen option says should change in the plan.
> 3. Read `<plan-dir>/<plan-name>.md` and verify each Chosen decision has a corresponding instruction in the plan text (fuzzy match on key phrases — exact wording not required).
> 4. Check that the LAST pass's `unresolved` list does NOT contain any title that also appears in the `resolved_dds` collected in step 1 (a stale carryover item).
> 5. Report any inconsistencies as a bullet list. If all coherent, return exactly "Coherent".
>
> Return the bullet list or "Coherent" directly (this subagent does NOT write a file — it returns findings inline).

If the subagent returns "Coherent", proceed to Steps 10-11 / finish. If it returns inconsistencies, present them to the user with AskUserQuestion offering:
- **Auto-fix** — spawn a corrector subagent to reconcile plan vs. review
- **Accept as-is** — user will handle manually
- **Show details** — print the full list and re-ask

This check runs regardless of early exit vs. hard cap. It is cheap (one subagent, ~1 min) and catches the class of bugs where the writer ran before DDs were resolved.

**Cleanup:** Once this step fully completes (including any Auto-fix corrector round), delete all files in `<plan-dir>/tmp/`, including `fix-manifest.md` — its data now lives in the review document and is no longer needed.

### Steps 10-11: Root-Cause Analysis & Skill Self-Improvement (conditional)

**Skip if this is the first review for the plan.**

If prior reviews exist and the current pass found findings that prior passes missed, read `.claude/skills/plan-reviewer/references/self-improvement-workflow.md` and follow Steps 6 and 7 from that file.

## Important Notes

### Orchestrator Context Budget

**The orchestrator must stay lean.** It is a dispatcher, not a reviewer. Follow these rules strictly:

- **NEVER read `coordinator.md`** (the full findings JSON). Read only `coordinator-summary.md`.
- **NEVER read or write the review document** directly. Delegate to the writer subagent (Step 7).
- **NEVER inline the full subagent prompt text.** Send the minimal template (Step 2) — subagents read the reference file themselves.
- **NEVER read the plan file** in the orchestrator. Subagents read it independently.
- The orchestrator's job is: launch subagents → read summary → apply mechanicals (Step 4) → present DDs to user (Step 5) → apply DD choices (Step 6) → run writer (Step 7) → report + loop (Step 8) → coherence check (Step 9). That's it.

### General Rules

- Each subagent reads source files independently — the main agent does NOT pre-read files for them
- All subagent launches must be in a single message for true parallelism
- If a subagent fails to return valid JSON, the coordinator (Step 3b) handles it as a FAIL with a parse error finding — do not attempt to handle it in the orchestrator
- Cite specific step numbers and file paths in every finding
- If the plan is solid, say so clearly — a clean review is a useful result
- Multiple reviews of the same plan accumulate in one file (append, don't overwrite — the writer subagent in Step 7 handles this)
- The review file lives at `<plan-dir>/reviews/` (beside the plan in the central store), named `<plan-name>-review.md`. Derive `<topic>` and `<plan-dir>` from the plan file's location (Step 1).
- **The coordinator subagent (Step 3b) guarantees non-contradictory mechanical fix items.** Any finding where two reviewers suggest incompatible plan edits to the same step is escalated to a design decision before the parallel fixing subagents run — so they never receive items that conflict with each other.

## Sync to Stronghold (final step — always run)

When all other work for this invocation is complete (all passes done, Step 9 coherence check finished, `<plan-dir>/tmp/` cleaned up), commit + push the stronghold plans-store / changelog changes to `main` via the central sync script (see `~/code/CLAUDE.md` → "Stronghold Auto-Sync (`.claude/scripts/stronghold-sync.sh`)"):

- Run `.claude/scripts/stronghold-sync.sh "<MESSAGE>"` — it is declared in `sandbox.excludedCommands`, so it runs unsandboxed for the SSH push automatically. Do **not** pass `dangerouslyDisableSandbox` (the DDS flag forces an un-suppressible permission prompt; the exclusion achieves the same unsandboxed run silently).
- `<MESSAGE>` = `plans(<repo>): plan-reviewer <plan-name>` — fill in the resolved `<repo>` (the `Plans bucket` value) and the `<plan-name>` of the plan reviewed this run.
- The script operates on the stronghold (`~/code`) regardless of your current working directory, stages only `plans/ changelog/ .claude/ CLAUDE.md` etc. (never a blanket add), and **skips cleanly if nothing changed** — so it is always safe to call even if this run wrote nothing to the stronghold.
- Do NOT open a PR or feature branch for this — the stronghold pushes direct to `main`. (This syncs only the stronghold; any sub-repo code changes keep their own normal git-commit/git-push/PR flow.)

## Handoff (final output — always the last thing printed, after the sync)

Print this block, filled from values already resolved this run (no extra tool calls):

```
Next step — plan reviewed. Copy/paste:

  /run-plan <repo>/<plan-name>

Repo: <repo-dir> · Branch: <plan-branch> · Worktree: <worktree-or-primary-clone> · Plan: ~/code/plans/<repo>/<open|completed>/<topic>/<plan-name>.md
Tip: /clear first — all state lives in the plan file, so a fresh context loses nothing and cuts token spend.
```

`<repo>` is the `Plans bucket` value, `<repo-dir>` the working repo's directory, `<plan-name>` the plan basename without extension, `<plan-branch>` the plan's `branch:` frontmatter (print `none yet — /run-plan creates it` when absent), `<worktree-or-primary-clone>` the `<worktree>` resolved in Step 1 (as `~/code/...`).
