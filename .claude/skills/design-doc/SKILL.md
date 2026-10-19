---
name: design-doc
description: Define WHAT a feature is and WHY before any implementation planning - grounds questions in parallel Sonnet codebase research, then resolves only the genuinely open product/UX/scope questions with the user via AskUserQuestion. Use for "define a feature", "write a design doc", "resolve open questions before planning". Writes under ~/code/plans/<repo>/open/<topic>/. Not for fully-specified refactors (use /plan-creator).
---

## Role in the planning chain

`/design-doc` answers **what and why**. `/plan-creator`, `/master-plan-creator`, and
`/master-plan-orchestrator` answer **how** (implementation steps, phasing, branches). Run this
skill first when a feature's *shape* is still open; skip it when the user already knows exactly
what they want built and only needs it broken into steps.

This skill's own execution **is** the orchestration — there is no separate hand-off agent (unlike
`/master-plan-orchestrator`, which hands its output to a long-running Fable 5 run). You, running
this skill right now, dispatch Sonnet 5 subagents for codebase research and ask the user directly
for anything a subagent can't resolve.

## Repo-specific values come from the seam

Every repo-specific value (the app's stack and UI conventions) is read at runtime from the
working repo's `## Claude Config` section (schema in `~/code/CLAUDE.md`).

**Resolve `<repo>` first.** Plans now live in a single **central store** in the stronghold, not
inside each sub-repo. Read the working repo's `## Claude Config` → **Repo slug** (e.g.
`ORG/app-main`) and take its basename (`app-main`) as `<repo>`. Plans about `~/code` itself use
the literal bucket `stronghold`. Never hardcode a repo name — always read the slug at runtime.
The **Plans store (central)** key in `## Claude Config` names this store; its value is
`~/code/plans/<repo>/`. New design docs ALWAYS start under `open/`, so every path written as
`plans/<topic>/...` below resolves to `~/code/plans/<repo>/open/<topic>/...`, with per-plan
`research/`, `reviews/`, and `mocks/` living BESIDE the doc in that same `<topic>/` dir.

## Step 1: Branch Guard

Follow `.claude/skills/plan-creator/SKILL.md` § Branch Guard verbatim.

## Step 2: Determine Topic Folder

Follow `.claude/skills/plan-creator/SKILL.md` § Step 1 verbatim. Store as `<topic>`.

If the user references an existing design doc or master plan by name (e.g. "add a design doc
for step 3 of the X master plan"), derive `<topic>` from that file's path instead of inferring
or asking.

## Step 3: Frame the Feature — What & Why

Capture a first-pass Problem / Why / Outcome directly from the user's request:

- **Problem** — what's missing, broken, or unclear today.
- **Why** — why this matters now (what prompted it).
- **Outcome** — what "done" looks like, in observable terms.

If the user's request already states these clearly, use them as-is. If any is thin or absent
(e.g. "add a bulk-export feature" with no stated motivation), ask the user directly — a single
targeted question, not a subagent — before proceeding. Don't invent a rationale on the user's
behalf.

## Step 4: Parallel Codebase Research (Sonnet 5 subagents)

Before asking the user anything about design specifics, ground the investigation in what
already exists. Read `references/research-prompts-design.md` for the full prompt set. Launch
all subagents **in parallel**, in a single message, each with `subagent_type: "researcher"` and
`model: "sonnet"` explicitly set (never omit them).

Create `~/code/plans/<repo>/open/<topic>/tmp/` first. The three research areas run as **two
agents**: Agent A covers 1 Existing Pattern & Convention Discovery and 2 Integration & Dependency
Surface, which read the same analog features. Agent B covers 3 Constraint & Precedent Discovery.
Each agent writes one file per area to
`~/code/plans/<repo>/open/<topic>/tmp/research-<focus>-design.md` and returns only one
`Written to <path>` line per file. Read each file after they return.

**Purpose:** every finding here either answers a question outright (no need to ask the user) or
narrows an open question to a real, specific decision point — turning "how should this work?"
into "should X behave like A or B, given the codebase already does A for the analogous feature
Y?"

## Step 5: Surface & Resolve Open Questions

From what Step 4's research did **not** settle, enumerate genuine open questions — decisions
that depend on user intent, product judgment, or preference, not facts about the codebase. Do
not ask a question the research already answered.

- Batch questions via `AskUserQuestion` (max 4 per call). If more than 4 remain, run additional
  rounds rather than dropping any.
- **Visual/UX questions get a mockup first.** If a question concerns layout, visual state, or
  interaction (not just behavior), produce a lightweight HTML mockup before asking — reuse
  `.claude/skills/plan-creator/SKILL.md` § UI Mockup Protocol machinery (rendered to PNG via
  Playwright MCP, shown with `SendUserFile`) so the user reacts to something concrete, not a text
  description. **Mockup fidelity** (which exact CSS/theme values and component/class conventions
  to match) is stack-dependent and defers to plan-creator's Stack-gated UI-Mockup reference:
  pull styling from the working repo's real CSS/theme values — never invented ones.
- **New UI must fit the app's existing UI, not stand apart from it.** Every visual/UX decision
  defaults to the app's existing color palette, component patterns, and interaction conventions
  (Step 4's `ui_conventions` findings are the grounding — never invent a color, spacing value, or
  interaction pattern with no precedent in the codebase). A genuine visual departure (a new
  color, a new interaction pattern) is itself a Design Decision requiring explicit user
  confirmation — never something a mockup introduces silently on the assumption it looks nicer.
- An answer can reveal a new open question or require a follow-up research check (e.g. "does
  the metrics/analytics system already support that dimension?") — loop back to Step 4 for a
  targeted single-subagent check if needed, then continue asking.
- Log every question, the options considered, the user's answer, and why, as you go — this
  becomes the design doc's **Design Decisions** section. Don't ask, get an answer, and discard
  the reasoning.
- If the user explicitly defers a question to implementation time, record it under **Open
  Questions (Deferred)** — never silently drop it.

Repeat until no genuine open question remains unresolved or explicitly deferred.

## Step 6: Write the Design Doc

Write to `~/code/plans/<repo>/open/<topic>/<name>-design.md` (kebab-case, ends in `-design`).
Inject `repo: <repo>` YAML frontmatter at the top of the file (alongside any other frontmatter).

```markdown
---
repo: <repo>
type: design
---

# <Feature Name> — Design Doc

## Problem
<from Step 3>

## Why
<from Step 3>

## Outcome
<from Step 3>

## Codebase Context
<3-6 bullets from Step 4's research: existing patterns, conventions, integration points this feature builds on or must respect>

## Design Decisions

### <Question>
**Options considered:** <A, B, ...>
**Decision:** <what was chosen>
**Why:** <user's stated rationale>

### <Next question>
...

## Non-Goals / Out of Scope
<explicitly excluded, to bound later phasing>

## Open Questions (Deferred)
<any question the user explicitly pushed to implementation time, with why — omit this section entirely if none>

## Mockups
<link every mockup produced: `[<name>](mocks/<name>.html)` — omit if none>

## Status
finished: true
```

A design doc is a complete deliverable the moment it's written — Step 5's loop only exits once every
open question is resolved or explicitly deferred, so there's no separate "implementation" phase for
this file to await. Always write `finished: true`, matching `/plan-creator`'s and
`/master-plan-creator`'s `## Status` block so `/plan-list` picks it up as done rather than unknown.

## Step 7: Cleanup & Handoff

1. Delete `~/code/plans/<repo>/open/<topic>/tmp/research-*-design.md`. Never delete
   `~/code/plans/<repo>/open/<topic>/mocks/` — mockups are permanent artifacts, same rule as
   `/plan-creator`.
2. Do **not** create a GitHub issue here. `/plan-creator`, `/master-plan-creator`, and
   `/master-plan-orchestrator` each create one already, and (per each skill's design-doc
   detection step) read this file's Problem/Why/Outcome directly instead of re-deriving it —
   currently wired into `/master-plan-orchestrator`.
3. Report: design doc path, number of decisions resolved, number deferred, mockup paths (if
   any — already sent via `SendUserFile` during Step 5).
4. Suggest the next command based on what the user described: `/master-plan-orchestrator`
   (multi-phase, single autonomous run), `/master-plan-creator` (multi-phase, per-phase human
   review), or `/plan-creator` (single PR).

Append a changelog entry per user-level CLAUDE.md rules (`design-doc: Created
~/code/plans/<repo>/open/<topic>/<name>-design.md — N decisions resolved, M deferred`).

## Step 8: Sync to Stronghold (final step — always run)

When all other work for this invocation is complete, commit + push the stronghold plans-store / changelog changes to `main` via the central sync script (see `~/code/CLAUDE.md` → "Stronghold Auto-Sync (`.claude/scripts/stronghold-sync.sh`)"):

- Run `.claude/scripts/stronghold-sync.sh "<MESSAGE>"` — it is declared in `sandbox.excludedCommands`, so it runs unsandboxed for the SSH push automatically. Do **not** pass `dangerouslyDisableSandbox` (the DDS flag forces an un-suppressible permission prompt; the exclusion achieves the same unsandboxed run silently).
- `<MESSAGE>` = `plans(<repo>): design-doc <topic>` — fill in the resolved `<repo>` (Repo slug basename) and the `<topic>` of the design doc written this run.
- The script operates on the stronghold (`~/code`) regardless of your current working directory, stages only `plans/ changelog/ .claude/ CLAUDE.md` etc. (never a blanket add), and **skips cleanly if nothing changed** — so it is always safe to call even if this run wrote nothing to the stronghold.
- Do NOT open a PR or feature branch for this — the stronghold pushes direct to `main`. (This syncs only the stronghold; any sub-repo code changes keep their own normal git-commit/git-push/PR flow.)
