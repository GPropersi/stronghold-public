---
name: pr-review
description: Review a GitHub PR against its plan with 8 parallel subagents. Writes a "PR Review for <plan>" record to the plan's reviews/ folder, raises design decisions via AskUserQuestion, and posts nothing to GitHub. Use to review or audit a PR against its plan. Argument is the PR number (omit to auto-detect from the current branch).
argument-hint: PR-number (optional — auto-detects from current branch)
---

# PR Review with Multi-Agent Review (Single Pass)

Review a GitHub PR against its corresponding plan using 8 parallel subagents. Produces a local review record only — does **not** post comments to GitHub. This is a single-pass review (no loop). For posting findings to the PR, use `/code-review --comment`; for review-and-push, use `/git-push`.

## Config resolution (read first)

This skill is repo-agnostic. Every repo-specific value comes from the **working repo's `## Claude Config`** section (schema in `~/code/CLAUDE.md`). Resolve these keys before running:

- **`<Token-generator>`** — the repo's **Token generator** command (from its `## Claude Config`). Every `gh` call runs as `GH_TOKEN=$(<Token-generator>) gh …`.
- **`<Repo-slug>`** — the repo's **Repo slug** (`<owner>/<repo>`), passed as `gh … --repo <Repo-slug>` when a call needs an explicit target.
- **`<repo>`** — the **basename of the Repo slug** (`ORG/app-main` → `app-main`; plans about `~/code` itself use the literal bucket `stronghold`). This keys the plan's bucket in the central plans store. Read it from the working repo's `## Claude Config` at runtime — never hardcode a repo name.
- **`<reviews-dir>`** — the plan's co-located `reviews/` folder in the central store (from the repo's **Plans store (central)** key: `~/code/plans/<repo>/{open,completed}/<topic>/reviews/`). Resolved in Step 2 once the plan's `<topic>` dir is known; the review always lives beside the plan it reviews in the central store, never in the app sub-repo's checkout.
- **`<Stack>`** — the repo's **Stack** tag set, used to load opt-in reviewer heuristics (see "Stack protocols" below).

If a key is missing, degrade gracefully: fall back to the ambient value (e.g. `gh` without `--repo`, auto-detecting from the current checkout) and note the assumption rather than erroring.

## Stack protocols (opt-in)

Reviewer heuristics that are genuinely stack-bound (framework/language idioms, specific doc/registry names, marker vocabulary) live in `references/stack/<tag>.md`. Read the working repo's `## Claude Config` → **Stack**. For each `references/stack/<tag>.md` whose declared tags intersect the repo's Stack, its per-subagent checklist is folded into the matching subagent (Subagents 2, 3, 5, 7, 8, and the `fix_type` examples). If the repo declares no Stack or none match, subagents apply only their stack-neutral cores. The subagent-prompts reference file tells each subagent to load the matching stack file itself.

## Workflow

### Step 1: Resolve the PR

If `$ARGUMENTS` is a number, use it as `<PR>`. Otherwise auto-detect from the current branch:

```bash
GH_TOKEN=$(<Token-generator>) gh pr view --json number,title,body,headRefName,baseRefName
```

Extract `<PR>`, `<branch>`, `<base>`, `<title>`, `<body>`. If no PR exists for the current branch and no arg was given, stop and inform the user.

All `gh` calls in this skill use the inline `GH_TOKEN=$(...)` prefix and run **in-sandbox** (the github.com hosts are allowlisted — never set `dangerouslyDisableSandbox` on `gh` calls).

### Step 2: Locate the Plan

Resolve `<plan-path>` for the PR in the **central plans store** — the plan is NOT in the app repo's PR/checkout; it lives in the stronghold store keyed by `<repo>`. Always search `~/code/plans/<repo>/open/<topic>/<plan-name>.md` FIRST, then `~/code/plans/<repo>/completed/<topic>/<plan-name>.md`:

1. **From PR body** — grep the body for any reference matching `<topic>/<name>.md` and resolve that topic/name against `~/code/plans/<repo>/open/…` first, then `~/code/plans/<repo>/completed/…`. If exactly one match exists and the file is present, use it.
2. **From branch slug** — strip the branch's leading segment (`frontend/foo` → `foo`) and Glob `~/code/plans/<repo>/open/**/*<slug>*.md` first, then `~/code/plans/<repo>/completed/**/*<slug>*.md`. If exactly one match, use it.
3. **Otherwise** — use `AskUserQuestion`:
   - `header`: `"Plan"` (max 12 chars)
   - `question`: `"Could not auto-detect a plan for PR #<PR>. Choose one or skip plan-conformance review."`
   - `options`: up to 3 plausible plan candidates (label = bare plan filename, description = parent topic folder) + a final `"Skip plan conformance"` option.
   - User-typed "Other" is allowed for a custom path.

If a plan was chosen, set `<topic-dir>` = the plan's parent `<topic>/` directory (under `open/` or `completed/` in the central store), `<topic>` = its basename, `<plan-name>` = plan filename without `.md`, and `<reviews-dir>` = `<topic-dir>/reviews/`. If the user selected "Skip plan conformance", set `<plan-path>` = null and ask a second question for where to file the review:
- `header`: `"Topic"` — options are the top 3 most recently-modified `~/code/plans/<repo>/open/*/` folders + `"Create new"` + Other. Use the chosen folder as `<topic-dir>` (`<topic>` = its basename), set `<reviews-dir>` = `<topic-dir>/reviews/`, and set `<plan-name>` = `<branch>` for review-file naming.

`<plan-path>` may be null for the rest of the workflow — that only disables Plan Conformance findings, nothing else.

### Step 3: Fetch the Diff

```bash
GH_TOKEN=$(<Token-generator>) gh pr diff <PR>
GH_TOKEN=$(<Token-generator>) gh pr view <PR> --json files --jq '.files[] | "\(.path) (+\(.additions)/-\(.deletions))"'
```

Create `<tmp-dir>` = `<topic-dir>/tmp/` (i.e. `~/code/plans/<repo>/{open,completed}/<topic>/tmp/`) (`mkdir -p`). Write the diff to `<tmp-dir>/pr-diff.patch` using the Write tool — never heredoc/redirect. Capture the file-list output in memory for subagent prompts.

If the diff is empty, stop and inform the user.

### Step 4: Launch 8 Parallel Review Subagents

Read `~/code/.claude/skills/pr-review/references/subagent-prompts.md` for the full prompt definitions, response format, and `fix_type` classification rules. Each subagent receives the minimal prompt template below — they read their checklist from the reference file (and, per its instructions, load the matching `references/stack/<tag>.md` for stack-bound heuristics).

Subagent prompt template (send to each, filling blanks):

> You are Subagent #N (<Role Name>) reviewing PR #<PR> on branch `<branch>`.
>
> 1. Read `~/code/.claude/skills/pr-review/references/subagent-prompts.md` — find your role's section and the shared response format. Follow it exactly. Then read the working repo's `## Claude Config` → **Stack** and, if any `references/stack/<tag>.md` declares tags intersecting it, fold that file's section for your role into your checklist.
> 2. The diff is at `<tmp-dir>/pr-diff.patch`. The PR title is "<title>" and body is below. Files changed: <file-list>.
> 3. <If plan exists:> The plan this PR is composed against is at `<plan-path>`. Read it in full before reviewing.
> 4. Read the source files relevant to your review area, plus the repo's `CLAUDE.md`.
> 5. Write your JSON response to `<tmp-dir>/<role>.md` using the `Write` tool — NEVER `cat <<EOF`, `python3 << 'EOF'`, `cat >`, `tee`, `printf >`, `echo >`, or any heredoc/redirect (brace+quote security prompt).
> 6. Return only: `Written to <path>`.
>
> PR body:
> <body>

Launch **all subagents in a single message** for true parallelism. Use `model: sonnet`. Do NOT use `subagent_type: "Explore"` — it cannot use the Write tool. Skip Subagent #1 (Plan Conformance) if `<plan-path>` is null.

| # | Name                       | Focus                                                                  | Output file                       |
|---|----------------------------|------------------------------------------------------------------------|-----------------------------------|
| 1 | Plan Conformance           | Implements plan steps; no skipped to-dos; no scope creep               | `<tmp-dir>/plan-conformance.md`   |
| 2 | Correctness                | Logic errors, race conditions, error-handling gaps, wrong API usage    | `<tmp-dir>/correctness.md`        |
| 3 | Code Patterns              | Matches the repo's established idioms (its CLAUDE.md + Stack file)      | `<tmp-dir>/code-patterns.md`      |
| 4 | Simplicity & Readability   | Over-engineering, dead code, unnecessary abstractions                  | `<tmp-dir>/simplicity.md`         |
| 5 | Test Coverage              | Happy + sad path tests, FE tests for FE changes, integration for BE    | `<tmp-dir>/test-coverage.md`      |
| 6 | Security & Safety          | OWASP, auth, input validation, SQL/XSS injection vectors               | `<tmp-dir>/security.md`           |
| 7 | Consistency & Style        | Import style, naming, no debug artifacts, module-top constants         | `<tmp-dir>/consistency.md`        |
| 8 | Documentation & Registry   | Registry/architecture/CLAUDE.md and doc updates the repo maintains     | `<tmp-dir>/documentation.md`      |

### Step 5: Coordinator Subagent

After all subagent files are written, launch a **single coordinator subagent** to deduplicate findings and escalate conflicts to design decisions. Reuse the coordinator prompt from `~/code/.claude/skills/pr-review/references/subagent-prompts.md` (the "Coordinator Subagent" section).

The coordinator writes `<tmp-dir>/coordinator.md` (full findings) and `<tmp-dir>/coordinator-summary.md` (verdicts, counts, DD titles, DD options). The orchestrator reads **only** the summary — never the full coordinator file.

### Step 6: Present Design Decisions via AskUserQuestion

Read `<tmp-dir>/coordinator-summary.md`. For each finding with `fix_type: "design_decision"`, present it via `AskUserQuestion`:

- `question`: `"DD-N: <finding title>?"` — include enough context for the user to decide
- `header`: `"DD-N"` (max 12 chars)
- `options`: from `design_decision_options` in the summary
- `multiSelect: false`

Constraints: max 4 questions per call, max 4 options each. Batch across multiple calls if needed.

Capture each chosen option (and any free-text notes) for the review file.

### Step 7: Write the Review File via Writer Subagent

Launch a **writer subagent** to produce the review record. The orchestrator never reads `coordinator.md` or writes the review file directly.

Review file path (`<reviews-dir>` = the plan's co-located `reviews/` folder in the central store, resolved in Step 2):
- **With plan**: `<reviews-dir>/<plan-name>-pr-review-<PR>.md`
- **Without plan**: `<reviews-dir>/pr-review-<PR>-<branch>.md`

`mkdir -p <reviews-dir>` if needed. **Never** write the review under any `tmp/` dir (`<topic-dir>/tmp/` or any transient tmp dir).

Writer subagent prompt:

> You are the PR review writer for PR #<PR>. Read `<tmp-dir>/coordinator.md` for the full findings.
>
> Write the review section to `<review-file-path>` using the `Write` tool (no heredoc/redirect). If the file already exists, append a new dated section — do not overwrite.
>
> **Title (first creation only):** `# PR Review for <plan-name> (PR #<PR>)` (or `# PR Review for PR #<PR> — <branch>` if no plan was provided). This title is mandatory and distinguishes the record from plan reviews and push reviews.
>
> **Resolved DDs:** apply the following user choices to the corresponding DD-N entries (fill `**Chosen:**` and the user note if any): <list of {DD-N → choice, note}>.
>
> **Document structure** (single dated section per run):
>
> ```
> ## Review — <YYYY-MM-DD HH:MM>
> PR: #<PR> — <title>
> Branch: <branch> → <base>
> Plan: <plan-path or "none">
>
> ### Summary
> 2-3 sentences on overall verdict and shape of findings.
>
> ### Subagent Results
> | # | Reviewer | Verdict | One-line summary |
> |---|---|---|---|
> | 1 | Plan Conformance | PASS/FAIL/N-A | ... |
> | ... |
>
> ### Findings
> Grouped by Critical / Major / Minor / Nit (omit empty categories). Each finding includes file:line, description, suggestion. For `duplicate` classification, append `*(flagged by: Reviewer A, Reviewer B)*`.
>
> ### Design Decisions
> #### DD-N: <title>
> **Context:** ...
> | # | Option | Trade-off |
> |---|---|---|
> | 1 | ... | ... |
> **Chosen:** <user's choice> <optional note>
>
> ### Verdict
> One of: **Ready to merge** / **Minor concerns** / **Requires changes** / **Blocked**.
> ```
>
> Return only: `Written to <review-file-path>`

Use `model: sonnet`.

### Step 8: Final Summary

Report to the user:
- Per-reviewer verdicts (one line each)
- Counts: critical / major / minor / nit
- Number of design decisions resolved
- Final verdict
- Path to the review file

Then clean up: delete all files in `<tmp-dir>/`.

## Important Notes

- **Single pass.** This skill runs the review once. To re-review after fixes, invoke `/pr-review <PR>` again.
- **Does NOT post to GitHub.** This is a local review record. Use `/code-review --comment` to post findings as inline PR comments, or `/git-push` for the review-and-push workflow.
- **Does NOT modify code or apply fixes.** Auto-fixing is `/run-review`'s job; this skill records findings only.
- **All subagent launches in a single message** for true parallelism.
- **Writer + coordinator subagents only** touch `coordinator.md` and the review file. The orchestrator reads only `coordinator-summary.md`.
- **`<tmp-dir>` is `<topic-dir>/tmp/`** (in the central store) — transient subagent communication only. Final documents (the review file) always live in the plan's co-located `reviews/` folder in the central store (its **Plans store (central)** location). Never store final review records in a `tmp/` dir.
- **All `gh` calls** use the inline `GH_TOKEN=$(<Token-generator>)` prefix and run in-sandbox (github.com hosts are allowlisted — never set `dangerouslyDisableSandbox` on `gh`).
- **Use Write/Edit for all file content.** Never heredoc or redirect (`cat <<EOF`, `python3 << 'EOF'`, `cat >`, `tee`, `printf >`, `echo >`) — brace+quote content trips the security prompt.
- **If a subagent fails to return valid JSON** or its output file is missing, the coordinator treats it as FAIL with a parse error finding.
