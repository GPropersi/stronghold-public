# Orchestrator Execution Prompt — Template

Used by `master-plan-orchestrator` Step 7. Fill in every `<bracketed>` placeholder from the
master plan just written (title, branch, topic, phase count, research findings) before writing
the result to `~/code/plans/<repo>/open/<topic>/<name>-execution-prompt.md` (`<repo>` = basename
of the working repo's `## Claude Config` **Repo slug**; `stronghold` for `~/code` itself).

The generated file has two parts:
- Everything **above** `## Prompt` — reference/resume scaffolding, never sent to the agent.
- The `## Prompt` section itself — handed **verbatim** to the top-level `Agent` call
  (`model: "fable"`, `subagent_type: "general-purpose"`). Do not paraphrase or summarize it when
  launching; pass its exact text.

This template is derived from a real 10-phase migration run that completed end-to-end with CI
green on the first attempt. Keep deviations from it deliberate, not accidental — every section
below exists because it prevented a specific failure mode (branch-per-phase drift, silent scope
creep, unverified progress claims, early stopping, premature removal of a legacy system before
its replacement is green, etc.).

**Repo-specific values come from the seam.** Fill placeholders like `<owner/repo>`, the push
script, and the verification commands from the working repo's `## Claude Config` section
(**Repo slug**, **Bot push script**, **Commands**, **Default branch**) — never hardcode one
repo's values into a generated prompt.

**Effort parameter note:** Anthropic's Fable 5 guidance treats `effort` (`high`/`xhigh`) as the
primary intelligence/latency/cost control for this model. This repo launches Fable 5 through the
`Agent` tool, which does not expose an `effort` parameter — there is nothing to fill in for it
here. If a run is ever launched directly via the Messages API instead of the `Agent` tool, set
`effort: high` (or `xhigh` for a phase flagged as the hardest in Research Findings).

---

## Template

```markdown
---
repo: <repo>
type: prompt
---
# Fable 5 Execution Prompt — <Title>

Reference material for running <initiative one-liner> as one unattended Fable 5 effort, with
Sonnet 5 subagents doing the mechanical per-file work. This file is the actual prompt handed to
the top-level `Agent` call (`model: "fable"`, `subagent_type: "general-purpose"`) — kept here so
it can be reused/rerun without reconstructing it, and so a resumed run has a fixed reference point.

## Status
finished: false

## Prompt

**Task:** Execute <initiative description> described in `<master-plan-path>` — all <N> phases,
end-to-end, autonomously, in one continuous effort. <If any phase(s) already have a fully detailed
sub-plan, name them and their path(s) here — those are elaborated already. Every other phase is
only branch/title + high-level bullets in the master plan and must be elaborated by you, in
place, as you reach it.>

### Step 0 — orient and set up memory

- Set up the single branch `<branch-name>` and its worktree (per `~/code/.claude/worktree-discipline.md` → Worktree
  Discipline, part (1)). `<worktree-path>` is resolved at runtime, never substituted at generation
  time. Resolve porcelain-first:
  - Run `git -C ~/code/<repo> worktree list --porcelain` from `~/code` first (use bare `git` instead
    when the repo is already the cwd — a redundant `-C` is blocked by a hook). Ignore any block that
    is `prunable` or whose path is missing, then branch on where `branch refs/heads/<branch-name>`
    appears:
    - In a non-primary block: that block's path is `<worktree-path>`. Add
      `worktree: <repo>/.claude/worktrees/<slug>` (stronghold-relative) to the master plan's
      frontmatter (`<master-plan-path>`) after `repo:`.
    - In the first (primary) block: `<worktree-path>` = `~/code/<repo>`; omit `worktree:`.
    - Checked out nowhere: fetch `origin/<default-branch>`
      (`git -C ~/code/<repo> fetch origin <default-branch>`), run
      `make wt-new REPO=<repo> BRANCH=<branch-name>` from `~/code` as a standalone Bash call (the
      sole branch/worktree creator; no compounding, no pipe), re-read porcelain, take the path of
      the `branch refs/heads/<branch-name>` block as `<worktree-path>`, and write `worktree:` as above.
  - If `wt-new` fails with `has no worktree support`, STOP and report "repo has no worktree support
    and `<branch-name>` is not prepared in the primary clone" — unless `<branch-name>` already exists
    and is the primary clone's current branch (check with `git -C ~/code/<repo> branch --show-current`),
    in which case proceed with `<worktree-path>` = `~/code/<repo>`
    and omit `worktree:`; never check out, switch, or create the branch directly. Any other
    `wt-new` failure (including a partial "step ... failed; worktree left in place"): STOP and
    report the hint `wt.sh` printed; never retry.
  - This one branch carries all <N> phases; there is no branch-per-phase.
  - The progress file, plans and changelog stay under `~/code/plans/<repo>/open/<topic>/` and
    `~/code/changelog`, never inside the worktree.
- Read the master plan <and any existing sub-plan(s)> in full. Cross-check their assumptions
  (exact file/line references, current helper contents) against the actual repo state before
  acting on them.
- Create and continuously maintain a memory file at `~/code/plans/<repo>/open/<topic>/<name>-progress.md`. This is
  your own persistent scratchpad for the entire run — if this run is interrupted and resumed,
  read this file first before re-deriving anything from git history. Structure it with:
  - A `## Status` section at the top: `finished: false`. This mirrors the master plan's own
    `## Status` block so `/plan-list` can track this file too — flip it to `true` only once the
    initiative reaches Definition of done, in the same edit that flips the master plan's.
  - A phase-by-phase status table (not-started / in-progress / done, commit range on
    `<branch-name>`, verification result).
  - A running decisions log — anywhere you deviated from a master-plan bullet and why.
  - A helper/pattern inventory — <domain-specific: e.g. every shared helper function added, so
    later phases reuse rather than duplicate>.
  - Known deferred/flagged items (e.g. anything needing a human to push, like CI-workflow
    changes under a protected path).
  Update this file after every phase and after any nontrivial decision.

### Per-phase execution

- For phases without a detailed sub-plan, elaborate the master plan's bullets into concrete
  steps yourself: read the actual current state of the target files/directories and adapt
  patterns already established in earlier phases rather than re-deriving from scratch. Do not
  produce a heavyweight sub-plan document per phase — scope inline as you go.
- **Single branch, not one per phase:** all <N> phases land on `<branch-name>` as sequential
  commits. This overrides the usual "each phase maps to its own branch/PR" convention — that
  convention assumes independent human-reviewed PRs merged one at a time; here the whole
  initiative is one continuous execution, so keep phase boundaries legible through clear,
  distinctly-labeled commits (e.g. a commit message prefix naming the phase, such as
  `<prefix>: <phase> — <what changed>`) rather than through separate branches.
- Delegate the bulk, mechanical work (<domain-specific: e.g. porting individual files or
  modules, group by group>) to Sonnet 5 subagents via your own `Agent` tool calls — pass
  `model: "sonnet"` explicitly on every nested call, never omit it. Keep for yourself: shared
  helper/pattern design, the highest-friction buckets called out in the master plan's research
  findings, and final phase-level verification.
- **Worktree discipline for every nested subagent.** Prepend this block verbatim to every nested
  Sonnet 5 subagent prompt (implementation, per-phase verifier, test/build runs), substituting
  `<plan-branch>` = `<branch-name>` and `<worktree>` = the `<worktree-path>` resolved in Step 0:

  > **WORKTREE DISCIPLINE (critical):** The working branch for this plan is `<plan-branch>`, checked out at `<worktree>`. Before reading or editing any file, running a build/test, or committing, run `git -C <worktree> branch --show-current` (bare `git branch --show-current` when `<worktree>` is already the cwd, e.g. `~/code` for stronghold plans — a redundant `-C` is blocked by a hook). Bash cwd does not persist between calls: use `git -C <worktree>` for every git command and absolute paths under `<worktree>` for everything else (builds/tests: `make -C <worktree> <target>` or `--project-directory <worktree>`, never a prior `cd`). If it is **not** `<plan-branch>`, **STOP and report back** to the orchestrator. **NEVER** run `git checkout`, `git switch`, or any command that creates a branch or worktree (`git checkout -b`, `git branch <name>`, `git worktree add`) to fix it. Edit sub-repo files only by absolute path under `<worktree>`; plans, reviews and the changelog stay under `~/code/plans` and `~/code/changelog`. Whatever the reason the branch is wrong (including `<plan-branch>` being checked out in another worktree), STOP and report back; never retry, force, or create a new branch.

  Your own direct file edits — work you keep for yourself rather than delegating to a nested subagent — must also use `<worktree-path>`-rooted absolute paths.
  Likewise run all of your own build, test and commit commands for the branch (including CI-fix
  commits) with `git -C <worktree-path>` / `make -C <worktree-path>` after asserting
  `git -C <worktree-path> branch --show-current` equals `<branch-name>` (Bash cwd may not persist
  between calls). The Bot push script and `gh` derive the repo from the cwd: run them after a
  standalone `cd <worktree-path>` Bash call only if your cwd persists, else STOP and report. No `<...>` placeholder may remain in a nested prompt after substituting
  `<plan-branch>` and `<worktree>`.
- **Capacity cap for concurrent nested subagents.** Before launching nested subagents that run
  builds/tests (they all run sharing the one worktree stack and host memory), read capacity with
  `make -C ~/code wt-capacity REPO=<repo>` as its own Bash call (output redirected to
  `/tmp/claude/wt-capacity-<repo>.txt`, then Read it and delete it), and keep the number of such
  subagents running at once `<= agents_max`. Read-only verifiers are not capped.
- Don't consider a phase done, and don't advance to the next, until its to-do items are
  complete **and** its verification has actually run and passed (the repo's per-phase
  verification command — pull it from the **Commands** table in `## Claude Config` / `CLAUDE.md`)
  — no progress claim without a command's real output backing it.
- **Independently verify each phase before advancing:** in addition to running the phase's
  verification command yourself, dispatch a fresh-context Sonnet 5 subagent (`model: "sonnet"`,
  no prior conversation state) to check the phase's diff against the master plan's bullets and
  this repo's conventions. A separate fresh-context verifier catches things self-critique
  misses. Only advance once both the command output and the verifier subagent agree the phase
  is correct.
- Commit at logical checkpoints within a phase. Once all <N> phases are complete and verified,
  stage, commit, and push the branch, then open a single PR covering the whole initiative — use
  the repo's **Bot push script** (never a bare `git push`), and reference the master plan's
  linked issue (`Closes #<issue-number>`) in the PR body. **Before writing the PR body, check for
  and follow the repo's PR template** — look for `.github/PULL_REQUEST_TEMPLATE.md` (also
  `.github/pull_request_template.md`, a repo-root `PULL_REQUEST_TEMPLATE.md`, `docs/`, or any
  `*.md` under `.github/PULL_REQUEST_TEMPLATE/`); `gh pr create --body` does NOT auto-apply it
  (GitHub only injects the template in the web-UI compose flow), so mirror its exact section
  headings/order as the body skeleton, fill each section with real content, tick only the
  checklist items you actually verified, and append the `Closes` line(s) below (filling any
  `Closes` line the template already carries in place). Only author a freeform body if the repo
  has no template. The PR itself is the review mechanism — do not add a separate pre-push human gate.
- **After the PR is open, watch CI to green.** Poll `gh pr checks <PR#> --repo <owner/repo>`
  (or `--watch`) until every check completes. For any failing check, fetch a distilled summary via
  `.claude/scripts/gh-log-fetch.sh --summary run view --job <job-id> --repo <owner/repo> --log-failed`
  (run from `~/code`) — read the summary; open the raw log path it prints only if the summary is
  insufficient. Then diagnose the actual root cause (never dismiss a CI failure as a pre-existing/flaky/unrelated
  issue without investigating it per this repo's failure-investigation rules — CI is running
  the exact same suites you verified locally, so a CI-only failure means a local/CI environment
  discrepancy, not a fluke), fix it, commit, push, and re-watch. Repeat until every check on the
  PR is green — this is part of the task, not a hand-off point.

### Judgment — default unattended, ask when it's a genuine fork

- Default to unattended, autonomous execution across all <N> phases — do not pause for a
  check-in between phases as a matter of routine.
- You have the `AskUserQuestion` tool available. Use it when you hit a genuine fork you cannot
  resolve yourself and that materially changes the diff or a downstream phase (e.g. a
  master-plan assumption has drifted enough that two reasonable paths exist with real
  tradeoffs). Don't use it as a routine checkpoint, and don't let being blocked turn into silent
  improvisation on something consequential — ask instead of guessing.
- You have ample context remaining. Do not stop, summarize, or suggest a new session on account
  of context limits. Continue the work.
- Before ending your turn, check your last paragraph. If it is a plan, an analysis, a question,
  a list of next steps, or a promise about work you have not done ("I'll…", "next I will…"), do
  that work now with tool calls instead. End your turn only when the task is complete or you are
  blocked on input only the user can provide.

### Boundaries

- Stay inside the initiative's scope: <domain-specific list, pulled from the master plan's
  Research Findings — directories, shared helpers/config, what's explicitly out of scope>.
  Don't touch unrelated application code.
- **Any UI-visible change must fit the app's existing UI — never stand apart from it.** Reuse
  the app's actual color palette (CSS custom properties/theme variables), existing component/
  modal/panel markup and class names, and established interaction patterns; never invent a new
  color, spacing value, or interaction pattern with no precedent in the codebase. Apply
  `.claude/skills/plan-creator/SKILL.md` § UI Mockup Protocol fidelity rules even though phases
  are elaborated inline rather than via `/plan-creator` — for a phase whose UI shape isn't
  already pinned down by a design doc or a master-plan mockup, produce and screenshot a
  lightweight HTML mock (real values, no invented styling) before implementing it. This is a hard
  constraint, not a judgment fork — resolve it yourself by grepping the actual theme/component
  conventions rather than pausing to ask; the answer is always "match what already exists," never
  a new look. (Omit this bullet entirely for a purely backend initiative with no UI phase.)
- <Any hard ordering constraint from research, e.g. "Phase <N> (removing the legacy system)
  only happens after every other phase's tests are verified green on the replacement — never
  take that shortcut early.">
- No tidying or refactors beyond what each phase's plan literally calls for.
- **Never wrap a command in a throwaway `.sh` in the scratchpad and run `bash <that-script>`.**
  That wrapper invocation is not allowlisted and fires a permission prompt to the user on every
  run (and it's the anti-pattern the SessionStart reminder warns about). Run build/test commands
  directly instead, addressing the directory explicitly (`make -C <dir> <target>`, `git -C <dir>`,
  absolute paths — Bash cwd does not persist between calls in subagents, and never compound
  `cd x && cmd`), then put any output redirect on the command itself, e.g.
  `npm run build > /tmp/claude/web-build.txt 2>&1` or `go test ./... > /tmp/claude/go.txt 2>&1`;
  any command redirecting into `/tmp/claude/` is pre-approved, so it runs prompt-free — then Read
  that file for results. **Instruct every Sonnet subagent you delegate to do the same** — the
  no-wrapper rule applies to their tool calls too, not just yours.
- Follow this repo's `CLAUDE.md` exactly — its dependency-pinning, import-style, naming,
  sandbox, and test-execution conventions. <Fill in the stack-conditional specifics that
  materially apply, pulled from this repo's CLAUDE.md — e.g. exact-pinned dependencies, absolute
  imports, no single-letter variables; on stacks whose test/build run through a container or
  build tool, any sandbox rule such as "`make`/`docker` calls need `dangerouslyDisableSandbox:
  true`"; tests always run via synchronous Bash (never `run_in_background`, never backgrounded);
  and any other domain-specific test convention, e.g. "UI tests always via the built-asset
  target".>

### Definition of done

- All <N> phases' to-do items complete and verified: tick every master to-do, and
  `~/code/.claude/scripts/plan-status.sh <repo>/<master-name> --verify` passes (exit 0).
- <Final full-suite verification command(s) from the repo's **Commands** table, matching its
  "Final Verification" convention — e.g. the full UI + integration suite green with the legacy
  system fully removed.>
- `<master-plan-path>`'s `## Status` updated to `finished: true`.
- `~/code/plans/<repo>/open/<topic>/<name>-progress.md` reflects the final state of every phase, and its own
  `## Status` is flipped to `finished: true` in the same pass.
- This execution-prompt file's own `## Status` (above `## Prompt`) is flipped to `finished: true`
  as well — all three files (`master`, `progress`, `execution-prompt`) move to `finished: true`
  together once the initiative is truly done, not just once the code merges.
- Once all three `## Status` blocks read `finished: true`, delete `open/<topic>/tmp/`, then run
  `~/code/.claude/scripts/plan-lifecycle.sh complete <repo-dir> <topic>` (never a hand-typed
  `git mv`): it moves the WHOLE topic directory `open/<topic>/` → `completed/<topic>/` (topic-as-unit —
  the folder location is the primary open/done signal). This initiative closes with a single PR, so the
  issue is closed by that PR's `Closes #<issue-number>`, not by the script.
- Everything lives on the single `<branch-name>` branch, pushed with a single PR covering the
  whole initiative — the PR is where review happens.
- Every CI check on that PR is green. The task isn't complete until CI passes — local
  verification alone doesn't satisfy this.

### Final summary

Whoever reads your final report did not watch this run happen — write it as their first look,
not a continuation of your working shorthand. Open with the outcome in one sentence (what
shipped, whether CI is green). Then supporting detail: phases completed, the PR link, anything
that deviated from the master plan and why, anything flagged for the user (e.g. a manual push
needed). Drop arrow-chain shorthand, invented labels, and terse fragments — spell out file names,
commits, and flags in plain sentences.

## Assumptions before running

- The push and PR happen as a single pass at the very end, not per-phase; the PR itself is the
  review mechanism, not a step gated behind a prior review.
- <Any other initiative-specific assumption worth recording — e.g. a pinned version choice, a
  known environment quirk discovered during research.>
```

## Fill-in checklist (do not skip any)

- [ ] `## Status` block (`finished: false`) is present above `## Prompt` — never omit it; it's how `/plan-list` tracks this file.
- [ ] `<Title>`, `<initiative one-liner>`, `<initiative description>`, `<master-plan-path>`, `<N>` — pulled directly from the master plan.
- [ ] `<branch-name>` — the single branch chosen in Step 4/5. Must match the master plan's `**Branch:**` line exactly. `<default-branch>` — the seam's **Default branch**. `<worktree-path>` — NOT filled in at generation time: leave it as the literal placeholder; the orchestrator resolves it at runtime in Step 0 (read from porcelain first, via `wt-new` only when the branch is checked out nowhere, or the primary clone `~/code/<repo>` when the branch is the primary's or on the `has no worktree support` fallback).
- [ ] Pre-existing detailed sub-plan(s), if any — name and path. Most initiatives have none; some (like a foundational first phase) may already have one written by `/plan-creator` before this skill ran.
- [ ] Per-phase verification command and final full-suite verification command(s) — pull from this repo's actual **Commands** table (`## Claude Config` / `CLAUDE.md` → Development Commands), not invented ones.
- [ ] `<owner/repo>` — the seam's **Repo slug**. The push script line — the seam's **Bot push script**.
- [ ] Domain-specific delegation buckets (what's mechanical/subagent-able vs what the orchestrator keeps for itself) — pull from the master plan's Research Findings, not assumed.
- [ ] Scope boundaries and any hard ordering constraint — pull verbatim from Research Findings; don't paraphrase away a real constraint (e.g. "don't remove the legacy system until everything else is green" is load-bearing, not decoration).
- [ ] If any phase touches user-visible UI, keep the Boundaries section's UI-fit rule intact (existing colors/components/interaction patterns, mock before implementing) — do not drop it as boilerplate for initiatives with no UI phase, but do not pad it with detail for initiatives that are purely backend either.
- [ ] Keep the "never wrap a command in a throwaway scratchpad `.sh`" Boundaries bullet verbatim (it's always-applicable, not stack- or UI-conditional) — including the clause that the rule extends to every delegated Sonnet subagent. Never drop it.
- [ ] `CLAUDE.md` conventions bullet filled with the working repo's actual stack-conditional rules (pinning, imports, sandbox/container, test-execution) — don't leave another repo's specifics in, and don't drop the bullet.
- [ ] `Closes #<issue-number>` — filled in only after Step 8 creates the issue; if Step 8 ran before Step 7, this is already known.
