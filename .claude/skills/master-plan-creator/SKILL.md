---
name: master-plan-creator
description: Creates a high-level master plan that decomposes a multi-PR initiative into numbered phases, each with a **Branch:** field. Use when the user asks to create a "master plan", "migration plan", "multi-PR plan", "umbrella plan", or a plan that "spans multiple branches/PRs/phases". Not for single-PR plans — use /plan-creator for those.
---

## Relationship to `/plan-creator`

This skill **reuses** `/plan-creator`'s shared machinery by reference — do not duplicate.
Authoritative sources (read these verbatim when the step below says "reuse"):

- **Branch Guard** → `.claude/skills/plan-creator/SKILL.md` § Branch Guard
- **Topic Folder determination** → `.claude/skills/plan-creator/SKILL.md` § Step 1
- **Research subagent harness** → `.claude/skills/plan-creator/SKILL.md` § Step 2 (pattern; see `references/research-prompts-master.md` for master-level prompts, not plan-creator's)
- **Research cleanup** → `.claude/skills/plan-creator/SKILL.md` § Step 3 Cleanup

What this skill **adds** on top:
- A master-scope research prompt set (broader than plan-creator's deep-dive)
- A `**Branch:**` field required on every non-verification step
- Coarser to-do granularity (sub-plan territory, not detailed implementation)

## Repo-specific values come from the seam

Every repo-specific value (**Repo slug**, **Default branch**, **Token generator**, **GitHub
project board** name, **Bot identity**, **Plans store (central)**, **Commands**) is read at
runtime from the working repo's `## Claude Config` section (schema in
`~/code/CLAUDE.md`). Refer to values by their seam-key name rather than a
literal. If a repo omits a key, fall back to the ambient default and note the assumption
rather than erroring.

**Resolve `<repo>` at runtime — never hardcode it.** Read the working repo's **Repo slug**
from its `## Claude Config` and take the basename (e.g. `ORG/app-main` → `app-main`). Plans
about `~/code` itself use the literal bucket `stronghold`. Plans live in the central store, not
inside the sub-repo: `~/code/plans/<repo>/`, with active plans under
`~/code/plans/<repo>/open/<parent-topic>/` and finished topics under
`~/code/plans/<repo>/completed/<parent-topic>/`. New master plans ALWAYS start under `open/`.
Per-plan `research/`, `reviews/`, `mocks/`, and transient `tmp/` are subfolders of the plan's
`<parent-topic>/` dir. Paths written as `~/code/plans/<repo>/open/<parent-topic>/` follow that
repo's **Plans store (central)** value.

## Step 1: Branch Guard

Follow `.claude/skills/plan-creator/SKILL.md` § Branch Guard verbatim.

## Step 2: Determine Parent Topic Folder

Follow `.claude/skills/plan-creator/SKILL.md` § Step 1 verbatim. Store as `<parent-topic>`.
This is the master plan's home (e.g. `openapi-typescript`). Sub-plan folders will be separate — do NOT nest sub-plans here.

## Step 3: Deep Research — Master Scope

Follow the subagent harness pattern in `.claude/skills/plan-creator/SKILL.md` § Step 2 (including: launch each research subagent with `subagent_type: "researcher"` and `model: "sonnet"`; NEVER use `subagent_type: "Explore"` — Explore agents cannot use the Write tool), with two differences:

1. Use master-level research prompts from `references/research-prompts-master.md` — NOT plan-creator's `references/research-prompts.md`. Master prompts focus on breadth (scope enumeration, phase boundaries, ordering, branch-naming conventions) rather than depth (signatures, data shapes).
2. Before launching, create `~/code/plans/<repo>/open/<parent-topic>/tmp/` (same as plan-creator).

The four research areas run as **two agents**, each writing one file per area. **Agent A** covers 1 Scope Enumeration and 2 Natural Phase Boundaries, which read the same surface. **Agent B** covers 3 Ordering Dependencies and 4 Branch Naming Conventions; area 4 is a small git-history read. Agents write to `~/code/plans/<repo>/open/<parent-topic>/tmp/research-<focus>-master.md` and return one `Written to <path>` line per file.

After subagents return, read each output file and compile findings for Step 4.

## Step 4: Propose Phases & Branches

Based on research findings, draft a numbered phase list where each phase:
- Represents a single PR / branch of work
- Has a suggested `**Branch:**` following the repo's observed naming conventions (from subagent 4)
- Respects ordering dependencies (from subagent 3)
- Is cohesive enough to review in a single PR

Present the proposed phases and branches to the user via `AskUserQuestion` with choices:
- **Accept as proposed** — proceed to Step 5
- **Modify** — user provides adjustments; redraft and re-present
- **Cancel** — abort skill

Do not write any plan file until the user accepts.

## Step 5: Write the Master Plan

Write to `~/code/plans/<repo>/open/<parent-topic>/<name>-master.md` (kebab-case filename ending in `-master`).

Use exactly this structure:

```markdown
---
repo: <repo>
---

# <Title>

## Summary
<2-4 sentence description of what this initiative does and why. Mention that each phase maps to its own branch/PR with a detailed sub-plan.>

## Research Findings
<3-6 bullets summarizing scope, phase boundaries, and notable constraints from subagent research.>

## Steps

### 1. <Phase title>
<One sentence describing what this phase accomplishes.>

**Branch:** `<branch-name>`

**To-do:**
- [ ] <Coarse bullet — a sub-plan will elaborate this into detailed tasks>
- [ ] <Coarse bullet>

---

### 2. <Phase title>
...

## Status
finished: false
```

**Rules:**
- Every step MUST have a `**Branch:**` line with a backtick-quoted branch name.
- To-dos are intentionally coarse — they name the work, not every file. Sub-plans elaborate.
- Do NOT include TDD Enforcement, Package Pinning, Dead Import Elimination, Function Signature Change, or Frontend/Backend Colocation sections. Those belong in sub-plans (`/plan-creator` enforces them).
- Do NOT include any test-verification step (per-phase OR a final umbrella one). Each sub-plan already runs the working repo's full suites (its **Commands** table — integration / UI / JS-unit tests) as its last step, so the master needs no separate verification phase.
- Separate steps with `---` horizontal rules for readability.

## Step 6: Validation

Before reporting success, verify:
1. Every step has a `**Branch:**` line matching the pattern `\*\*Branch:\*\* \`[^\`]+\``. Use Grep on the master plan file.
2. The `## Status` block is present with `finished: false`.

If any check fails, fix the plan file. Do not proceed to Step 7 until both pass.

## Step 7: Create Umbrella Issue

Create a GitHub issue that represents the entire multi-PR initiative — this is the **parent** of every phase. Each phase's sub-plan issue (created on-demand later by `/plan-creator`) is attached to this umbrella as a **native GitHub sub-issue**, so the umbrella renders the full phase tree in GitHub's hierarchy; sub-plan issues additionally carry a `Part of #<umbrella>` body backref. The umbrella closes automatically when the final sub-PR's `Closes #<umbrella>` lands on merge (driven by `/git-push`).

### 7a. Generate body and labels

Body structure:

```markdown
## Problem
<one paragraph: what's broken / missing at the initiative level>

## Why
<one paragraph: why this multi-PR effort matters now>

## Outcome
<one paragraph: what "done" looks like across all phases>

## Phases
- Phase 1: <Phase title> — branch `<branch-name>`
- Phase 2: <Phase title> — branch `<branch-name>`
- ...
---
Master plan: `~/code/plans/<repo>/open/<parent-topic>/<name>-master.md`
```

Pull phase titles and branch names directly from the master plan you just wrote.

Infer labels using the same table in `.claude/skills/plan-creator/SKILL.md` § Step 4b, applied across ALL phases. Umbrella issues typically get more labels than a single sub-plan.

### 7b. Create the issue

Use the working repo's **Token generator** command to mint the token and its **Repo slug** as
the `--repo` target — both read from `## Claude Config`, never hardcoded:

```bash
GH_TOKEN=$(<Token generator>) gh issue create \
  --title "Umbrella: <master-plan-title>" \
  --body "<generated-body>" \
  --label "<label1>" --label "<label2>" \
  --repo <Repo slug>
```

Capture `<issue-number>` and `<issue-url>`.

### 7c. Project board + bot assignee

Add the umbrella issue to the repo's project board and assign the bot, exactly as `/git-push`
does. Reuse `.claude/skills/plan-creator/SKILL.md` § Step 4c, which performs this same operation.

**Never inline raw GraphQL IDs.** The project / status-field / option / bot-node IDs are resolved
**at runtime by name** — from the seam's **GitHub project board** name and **Bot identity** login
— via `gh api graphql`, exactly as `/git-push` does; defer to `/git-push`'s runtime lookup rather
than storing any `PVT_…` / `PVTSSF_…` / `BOT_…` value. (If the seam omits **GitHub project
board**, skip board attachment and note it.)

While doing so you fetch the umbrella issue's GraphQL node ID. **Capture it as `<umbrella-node-id>`** — Step 7d persists it so `/plan-creator` can attach each phase issue as a native sub-issue without re-fetching.

### 7d. Write frontmatter

Merge these keys into the existing YAML frontmatter at the top of `~/code/plans/<repo>/open/<parent-topic>/<name>-master.md` (the `repo: <repo>` key from Step 5 is already there — add alongside it, do not replace it):

```yaml
---
repo: <repo>
github_issue: <N>
github_issue_url: <url>
github_issue_node_id: <umbrella-node-id>
---
```

This is what `/plan-creator` § Step 4d reads when creating sub-plan issues to discover the umbrella, attach the new phase issue to it as a native GitHub sub-issue (via `github_issue_node_id`), and append the `Part of #<N>` body backref. If `github_issue_node_id` is ever missing (e.g. a hand-written master), `/plan-creator` falls back to fetching it from the issue number.

### Skip rules

- **No** existing-issue search — umbrellas are always new.
- **No** master-parent detection — masters have no parent.

### Failure handling

Same pattern as plan-creator: surface the error and manual command, do not block the master plan file from existing.

## Step 8: Cleanup & Summary

1. Delete all `~/code/plans/<repo>/open/<parent-topic>/tmp/research-*-master.md` files.
2. Report to the user:
   - Master plan path
   - Number of phases created
   - Umbrella issue: `#<N>` and URL (or warning if Step 7 failed)
   - Do not print the next command here: the final "Next-Command Handoff" block (after the sync, below) prints it last.

Note: Sub-plan folders are created on-demand by `/plan-creator` when each phase's sub-plan is written.

Append a changelog entry per user-level CLAUDE.md rules (`master-plan-creator: Created ~/code/plans/<repo>/open/<parent-topic>/<name>-master.md with N phases, umbrella issue #<N>`).

## Step 9: Sync to Stronghold (final step — always run)

When all other work for this invocation is complete, commit + push the stronghold plans-store / changelog changes to `main` via the central sync script (see `~/code/CLAUDE.md` → "Stronghold Auto-Sync (`.claude/scripts/stronghold-sync.sh`)"):

- Run `.claude/scripts/stronghold-sync.sh "<MESSAGE>"` — it is declared in `sandbox.excludedCommands`, so it runs unsandboxed for the SSH push automatically. Do **not** pass `dangerouslyDisableSandbox` (the DDS flag forces an un-suppressible permission prompt; the exclusion achieves the same unsandboxed run silently).
- `<MESSAGE>` = `plans(<repo>): master-plan-creator <name>-master` — fill in the resolved `<repo>` (Repo slug basename) and the `<name>-master` of the master plan written this run.
- The script operates on the stronghold (`~/code`) regardless of your current working directory, stages only `plans/ changelog/ .claude/ CLAUDE.md` etc. (never a blanket add), and **skips cleanly if nothing changed** — so it is always safe to call even if this run wrote nothing to the stronghold.
- Do NOT open a PR or feature branch for this — the stronghold pushes direct to `main`. (This syncs only the stronghold; any sub-repo code changes keep their own normal git-commit/git-push/PR flow.)

## Next-Command Handoff (final output — always the last thing printed, after the sync)

Print this block from values resolved this run (no extra tool calls):

```
Next step — master plan written. Copy/paste:

  /plan-reviewer <repo>/<master-plan-name>

Repo: <repo-dir> · Branch: <plan-branch> · Plan: ~/code/plans/<repo>/open/<parent-topic>/<master-plan-name>.md
After the master review, write each phase's sub-plan: /plan-creator "Step 1 of <master-plan-name>"
Tip: /clear first — all state lives in the plan file, so a fresh context loses nothing and cuts token spend.
```

`<repo>` is the `Plans bucket` value, `<repo-dir>` the working repo's directory, `<master-plan-name>` the master plan basename without extension, `<plan-branch>` the master's `branch:` frontmatter if present, else `per phase (each step's **Branch:** field)`.
