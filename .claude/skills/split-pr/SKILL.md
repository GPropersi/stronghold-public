---
name: split-pr
argument-hint: [PR# or branch] [--pre-pr] [--handoff <file>]
description: Decompose one oversized, already-green PR into N smaller PRs that merge sequentially. Use for "split this PR", "this PR is too big". HARD PRECONDITION - only after /git-push opened the PR and its CI is fully green (failing goes to /pr-review-fixer), or as /git-push Step 7e's pre-PR handoff (--pre-pr) for a reviewed branch whose push gate passed at HEAD.
---

# Split a Green PR into Sequential PRs

Plan granularity is PR granularity, 1:1, so a 5-7 step plan lands as one 3,000-7,000-line PR that is
effectively unreviewable. This skill runs *after* the fact: it takes a branch whose CI has already
gone green and decomposes it into N smaller PRs, each cut fresh from the then-current default branch
and merged **sequentially**.

Splitting after green CI is what makes the operation safe. There is a known-good end state to
preserve, so the whole thing reduces to two checkable invariants:

1. **Union** — the parts together must reproduce the original tree exactly (tree-hash equality).
2. **Prefix greenness** — every prefix of the merge order must be independently green.

Neither is knowable before CI runs, which is why this is a post-push skill and not a plan-time one.
**Pre-PR mode (`--pre-pr`):** `/git-push` Step 7e hands off a reviewed branch whose push gate passed at
HEAD; that gate result stands in for green CI and nothing is re-reviewed. The parts still ship one at
a time through Steps 4-5. Its deltas to Steps 1a, 1c, 1e, 5d, 5e, 6a and 6b live in
`references/pre-pr-mode.md`; read it first in that mode. `--handoff <file>` (pre-PR only) names the
`split-handoff.md` that `/git-push` Step 7e wrote so a fresh session can start with no other context;
it supplies the branch, repo-dir and entry evidence (see "Handoff file" in that reference).

> **Repo-agnostic.** This skill reads the working repo's `## Claude Config` section (schema in
> `~/code/CLAUDE.md`) for every repo-specific value — **Repo slug**, **Default branch**, **Token
> generator**, **Bot push script**, **PR reviewer**, and the **Commands** table. Refer to those keys
> by name; never hardcode a repo's literals. If a repo omits a key, degrade gracefully per the
> fallback table below: fall back to the ambient value and note the assumption rather than erroring,
> except where that table says STOP.

> **Central plans store (resolve `<repo>` first).** All plans, reviews, research and this skill's own
> split artifacts live in **one tracked store in the stronghold** (`~/code/plans/`), never inside the
> sub-repos. Before any file path below, resolve `<repo>` = the value of the working repo's **Plans
> bucket** seam key (`## Claude Config` in its `CLAUDE.md`; the directory name under `~/code/plans/`;
> a missing key means STOP and report it, no fallback — it is explicit, never inferred, even though
> it currently equals the Repo slug basename for every repo). `<repo-dir>` = the working repo's
> directory name (the basename of its primary clone under `~/code`), used in `~/code/<repo-dir>`
> paths. `<plan-dir>` = the plan's topic folder, `~/code/plans/<repo>/open/<topic>/` (search
> `completed/<topic>/` as a fallback). This skill's durable artifacts live at `<plan-dir>/split/`.
> Full rules: `~/code/CLAUDE.md` → "Central Plans Store".

> **Precondition: you MUST be inside the working checkout of the SUB-REPO, not the stronghold.**
> Every `gh` call in this skill goes through `.claude/scripts/gh-bot.sh`, which resolves its GitHub
> App installation from **the cwd's git root**, not from a `--repo` flag — so a `gh-bot.sh` call made
> from `~/code` authenticates against the stronghold and fails cryptically. Likewise every `git diff`,
> `git apply`, `git merge` and `git worktree` call runs against the cwd. `cd` into the sub-repo
> checkout as its own Bash call (never compound `cd … && …`) before any such command, and `cd` back
> into `~/code` as its own call before each `.claude/scripts/stronghold-sync.sh` invocation. If
> `git rev-parse --show-toplevel` resolves to `~/code` (equivalently `git remote get-url origin` is
> the stronghold's `OWNER/stronghold`), you are in the stronghold: STOP and target the sub-repo. This
> skill never runs `git checkout <existing-branch>` or `git switch` to reach a branch; the only
> branches it creates are the per-part branches in Skill Step 5, each cut fresh from the
> then-current `origin/<default-branch>`.
>
> **Settled:** `gh-bot.sh` lives only in the stronghold, so the relative spelling the other allow
> entries use does not resolve from a sub-repo cwd. **Always spell it
> `~/code/.claude/scripts/gh-bot.sh`** — it has its own allow entry and still resolves the App from cwd.

## Seam keys this skill reads

| Seam key            | Used for                                                                                                   | Fallback when absent                                                                                                                                      |
| ------------------- | ---------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Repo slug**       | `<owner>/<repo>` in every `gh-bot.sh pr`/`api` call and in the cross-links between parts                   | Derive from `git remote get-url origin` and state the assumption                                                                                          |
| **Default branch**  | the protected branch each part branch is cut from — fetched fresh before every part                        | Read `origin/HEAD`'s target; if that is unset, assume `main` and state the assumption                                                                     |
| **Token generator** | the `GH_TOKEN=$(<token-generator>)` prefix on bare `gh pr *` calls, and `GH_TOKEN_GENERATOR=` on log fetch | **STOP and report** — a bare `gh pr *` is hard-blocked by `block-unsafe-push.sh` for an app-token repo, so there is no degraded path                      |
| **Bot push script** | pushing each part branch                                                                                   | `Bot identity: n/a` means personal-collaborator auth: plain `git push` as its own Bash call with `dangerouslyDisableSandbox: true` (SSH), nothing chained |
| **PR reviewer**     | the reviewer requested on every part PR                                                                    | `OWNER` — `~/code/CLAUDE.md` requires that reviewer on every PR in every repo, including OWNER-owned personal ones; never skip it                 |
| **Commands**        | the `make` targets for each part's local green gate (Skill Step 4)                                         | **STOP and report** — per the Makefile-First policy a missing target is created first; the gate must never be faked with raw commands                     |

`<default-branch>`, `<token-generator>`, `<bot-push-script>` and `<Repo slug>` are placeholders below.

## Prerequisite permissions (and what happens without them)

This skill's build pipeline needs five git commands in neither `settings.json`'s allowlist nor
`allow-git-ops.sh`'s safe set: `git apply`, `git merge`, `git format-patch`, bare `git merge-base` and
bare `git rev-parse` (only the `git -C * …` spellings of the last two were ever allowlisted). It also
needs `GH_TOKEN_GENERATOR=…gh-log-fetch.sh`, a `gh-bot.sh api -X DELETE repos/*` shape (the existing
`gh-bot.sh api repos/*` entry's single trailing wildcard does not match `-X DELETE`), and the absolute
`~/code/.claude/scripts/gh-bot.sh` spelling — **eight** `permissions.allow` entries in
`~/code/.claude/settings.json`, the only available lever (`.claude/hooks/*.sh` sit under
`permissions.deny` `Edit(...)`, so a hook's safe set cannot be edited). Changes go through
**`/update-config`**.

**Contingency — if those entries are absent or were declined.** Each of those calls then pauses for
interactive approval *every time it runs*, dozens of times across N parts. So:

- **Never run a step that calls one from inside an unattended `/loop` tick** — nobody is there to
  answer, so it stalls or silently abandons the run mid-split, the worst possible failure point for an
  operation that cuts branches and opens PRs. Prohibited under `/loop`: **Step 1** (provenance +
  base-pinning), **Step 3** (verification build + union invariant), **Step 5** (real build + CI log
  fetch) and the plan's **dry-run verification (9a-9c)**. Run those **interactively** instead.
- Waiting on CI (the Monitor tool or `/loop` dynamic mode) is still fine — the *build* and *log-fetch*
  calls prompt, not the wait.

## Workflow

### 1. Preconditions and provenance capture

Everything that must be recorded **before** any branch is cut, because cutting destroys it. Every
command in this step runs from inside the sub-repo checkout `~/code/<repo-dir>` (see the cd
precondition above) except 1g's stronghold sync.

**1a. Resolve the target.** The argument is a PR number or a branch; with no argument, default to the
**current branch's** PR. `cd ~/code/<repo-dir>` as its own Bash call **first** — `gh-bot.sh` picks its
App installation from **the cwd's git root**, not from `--repo`, so a call made before the `cd`
authenticates against the stronghold and fails cryptically. Then resolve the PR with
`gh-bot.sh pr view <PR#-or-branch> --repo <Repo slug> --json number,headRefName,headRefOid,state`.
Spell the wrapper `~/code/.claude/scripts/gh-bot.sh` (preamble), or use the equally prompt-free,
cwd-independent `GH_TOKEN=$(<token-generator>) gh pr view <PR#> --repo <Repo slug> --json
number,headRefName,headRefOid,state` for this read-only lookup. Record the resolved PR number, head
branch (`<branch>`) and head SHA — 1e archives all three. Then resolve `<repo>`, `<plan-dir>` and the
plan file for `<branch>`. A branch with no plan in the central store is not a blocker, but say so:
Skill Step 2's `plan-steps` column will then read `none` for every part.

**1b. Check for an in-progress split before doing anything else.** If
`<plan-dir>/split/split-manifest.md` already exists, this is a **resume, not a fresh start**: parse it
and resume from the per-part **`status`** column (`planned | built-and-verified | pushed | merged`;
schema in Skill Step 2) rather than re-running the slice planner and re-deriving a split from
scratch. Resume at the first part whose `status` is not `merged`, and skip **exactly** the work that
status value already covers:

| `status`               | What it already proves                                                                                          | Where to resume                                                                                                              |
| ---------------------- | --------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `planned`              | nothing has been proven for this part yet                                                                       | run Skill Steps 3, 4 and 5 in full                                                                                           |
| `built-and-verified`   | Step 3's build and Step 4's local gates passed; Step 4's own cleanup already removed that part's worktree/patch | **skip Steps 3 and 4 entirely**; resume at Step 5 (cut the branch, rebuild the patch, commit, push, open PR)                 |
| `pushed`               | the branch is cut and pushed and the PR is open                                                                 | skip Step 5's branch-cut / patch-rebuild / push / PR-open bullets; resume at its CI-wait and merge-confirmation bullets only |
| `merged`               | this part is fully done                                                                                         | skip it entirely; advance to the next part in the sequence                                                                   |

The forward reference is legal: `status` is *defined* in Skill Step 2 but only *read* here on a later
invocation — one where a prior run already reached Step 2 and wrote the manifest — so it always exists.

**Never trust a stale `pushed` row.** A row goes stale when the process was interrupted right after a
human merged the PR but *before* the skill recorded it. For the most recent part whose `status` is
`pushed`, verify programmatically — before deciding whether to advance to Skill Step 5's next-part
cut — with `GH_TOKEN=$(<token-generator>) gh pr view <part-PR#> --json state,mergedAt`. The
`GH_TOKEN=$(...)` prefix is mandatory for the reason given in 1c, not a style choice. Fallback:
`git cherry origin/<default-branch> <part-branch>` (a commit marked `-` is already merged). Write
`merged` into that row **only** once the check confirms it.

A resume still re-runs 1c (green CI) and 1d (pin the base) for idempotent safety — but see 1d's
resume guard: it must never silently recompute and overwrite the recorded base.

**1c. Hard-gate on green CI** (pre-PR mode: `references/pre-pr-mode.md`'s entry check replaces 1a and 1c). Run `GH_TOKEN=$(<token-generator>) gh pr checks <PR#>`.

- The `GH_TOKEN=$(...)` prefix is **mandatory**. A bare `gh pr checks <PR#>` is *hard-blocked* by
  `block-unsafe-push.sh` on an app-token repo — not merely unapproved — and `settings.json`
  allowlists exactly `Bash(GH_TOKEN=*gh pr checks*)`, so the prefixed form is also the only one that
  runs prompt-free. (`block-command-substitution.sh` exempts a leading `GH_TOKEN=$(` precisely for
  this.) This matches every other `gh pr *` invocation in `git-push/SKILL.md`.
- **Refuse to proceed unless every check has concluded successfully.**
- **Still running** → wait with the **Monitor tool** or **`/loop` dynamic mode**, re-running the same
  command on each tick. **Never a shell poll loop:** `block-polling-loops.sh` denies
  `until`/`while`/`for … do … sleep N … done`, and a foreground `sleep` is blocked outright.
- **Any check failed** → **stop** and direct the user to **`/pr-review-fixer`**. Do not split a red
  PR: the entire safety argument of this skill is that there is a known-good end state to preserve.

**1d. Pin the base.** `git fetch origin` first so the ref is current, then run bare
`git merge-base origin/<default-branch> <branch>` (allowlisted as `Bash(git merge-base:*)`) and read
its plain stdout — a single 40-character SHA. Write that literal SHA as text into the manifest's
`base-SHA` column and into the provenance archive immediately.

**Never assign it to a shell variable.** The harness's Bash tool does not persist shell state between
calls, so a variable set in one call is gone in the next; and `$(...)` command substitution is
hard-blocked by `block-command-substitution.sh` regardless (its only exemptions are `GH_TOKEN=$(`,
`TOKEN=$(`, `BRANCH=$(git branch`, `generate-gh-token.sh`, `gh-app-push.sh`, and the heredoc commit
form). Throughout this skill, every command that needs the base **substitutes the literal recorded
SHA string directly** — written `BASE` in the command examples here, meaning *paste the 40
characters*, never `$BASE`. Every subsequent diff, patch and invariant check is taken at this pinned
literal SHA; **a moving base is the primary source of spurious invariant failures.**

**Resume guard.** If `split-manifest.md` does not exist yet, compute and record `BASE` as above. If it
already exists (a resume), do **not** recompute and overwrite it: read the persisted `base-SHA` from
the manifest as the literal value to use, then *separately* run
`git merge-base origin/<default-branch> <branch>` once more and compare its stdout against that
persisted value **as plain text**. If they differ, **stop and report** rather than silently
overwriting — a mismatched base means `origin/<default-branch>` diverged from what the in-progress
split was built against, and continuing would silently corrupt the union invariant.

**1e. Archive provenance into `<plan-dir>/split/`** — a permanent artifact folder beside the plan,
**not** `tmp/` — because a later part build cannot recover any of it:

| Artifact                 | Source                                                                                                                                                                                                                                   |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `original-series.patch`  | `git format-patch --stdout BASE..<branch>` — the full original commit series                                                                                                                                                             |
| `original-log.txt`       | `git log --format=fuller BASE..<branch>` — authorship and trailers                                                                                                                                                                       |
| `review-threads.md`      | the original PR's review threads, exported via `gh` **before any branch is cut**                                                                                                                                                         |
| `original-pr.md`         | the original PR number, its head SHA, the pinned literal `BASE`, and the green check-run IDs — `GH_TOKEN=$(<token-generator>) gh api repos/<Repo slug>/commits/<head-SHA>/check-runs` (`gh pr checks` reports names and states, not IDs) |

Export the review threads with the same `reviewThreads` GraphQL query `pr-review-fixer/SKILL.md`
already carries (the REST API does not expose resolution status, so GraphQL is required) — but keep
**all** threads here, resolved included: this is an archive, not a work queue.

**This must happen before any branch is cut.** GitHub anchors each review thread to a
(SHA, path, line) triple in its UI; re-cutting the branch orphans that anchoring **even though the
underlying commits stay fetchable** via the PR's `refs/pull/<N>/*` ref. The commits are recoverable;
the thread anchoring is not. This is a demonstrated, reproduced failure, not a theoretical one.

**1f. Self-check each artifact immediately after writing it, before any branch is cut.**

1. `test -s <file>` on every archived file — confirm it is non-empty.
2. Confirm the archived format-patch applies cleanly, **at the base**. Running `git apply --check`
   against whatever branch happens to be checked out in the primary tree is the **wrong tree** and
   would falsely fail every time — the patch was taken *at* the base, so it must be checked *at* the
   base. Create a scratch worktree pinned at the literal base SHA the same way Skill Step 3 creates its
   per-part ones — **`git worktree add` is hook-denied in every sub-repo**, so it must go through the
   repo's own worktree target with a base-ref override (`references/invariant-checks.md` §2) — one Bash
   call each:
   - `make worktree-new name=split-base-check b=split/base-check from=<literal-base-SHA>` (app-main's
     spelling; substitute the repo's own target). Call the resulting path `<base-check-wt>`.
   - `cd <base-check-wt>`
   - `git apply --check <plan-dir>/split/original-series.patch` — **bare**, not `-C`-prefixed: the
     bare form is what `Bash(git apply:*)` matches, while `git -C <path> apply …` shifts past that
     allow pattern and prompts. That is exactly why the `cd` is its own separate call.
   - `cd ~/code/<repo-dir>` — before the removal below.

   This `cd`-then-check sequence relies on the Bash tool's cwd persisting across calls — confirm it
   does in the running context (**a subagent thread resets cwd between Bash calls**); a check that
   silently ran in the primary tree is exactly the false failure this guards against. If cwd does not
   persist, use `git -C <base-check-wt> apply --check …` and accept the one approval prompt instead.
3. **Remove the worktree and its branch immediately after the check** — `git worktree remove
   <base-check-wt>` (never `--force`) and `git branch -D split/base-check`. This is a cleanup
   obligation of *this* step, alongside the `test -s` checks; never leave it for a later step.

**Fail loudly — stop and report — on any check that does not pass.** Never proceed to Skill Step 2
with an unverified provenance archive.

**1g. Sync to stronghold.** Once the archive is written and self-checked, one Bash call each:

1. `cd ~/code` — never a compound `cd … && …` (`block-compound-commands.sh`). This step's other work
   runs inside the sub-repo, mirroring `git-push/SKILL.md`'s final sync step.
2. `git rev-parse HEAD` — note its literal output.
3. `.claude/scripts/stronghold-sync.sh "<message>"` — **no** `dangerouslyDisableSandbox` (the script is
   allowlisted and sandbox-excluded); keep the message free of `;` `&&` `||` `$(` and backticks.
4. `git rev-parse HEAD` again — compare its literal output against the value noted in (2) **as plain
   text**, never captured into a shell variable (1d's convention). A changed SHA means the sync
   committed. If instead the script's own output explicitly said there was **nothing to commit**,
   confirm that message rather than expecting a SHA change. **Stop and report if neither holds** — an
   unsynced archive is an unprotected archive, and the next steps start cutting branches.
5. `cd ~/code/<repo-dir>` — before Skill Step 2's work resumes.

**Write every artifact this step composes with the Write tool.** `block-bash-writes.sh` blocks
`cat > file`, `cat <<EOF`, `tee <file>`, `echo > file` and `printf > file`, so the manifest,
`review-threads.md` and `original-pr.md` are Write-tool files. The two raw-git captures are ordinary
redirects of git output rather than those blocked shapes, and
`Bash(git format-patch:*)` / `Bash(git log:*)` match regardless of the redirect target; if either
still prompts, redirect into `/tmp/claude/split/` (pre-approved by `allow-tmp-access.sh`, which also
pre-approves the `git diff > file` captures used from Skill Step 2 onward) and re-emit the content
into `<plan-dir>/split/` with the Write tool.

### 2. The slice planner

The intellectual core: assign every changed file to a part, in a legal order, and get the user's
acceptance before anything is built. **Delegate the analysis; own the gate and the manifest.**

**2a. Produce the input.** From inside `~/code/<repo-dir>`, substituting the **literal** pinned `BASE`
SHA from 1d (never `$BASE`), one Bash call each:

- `git diff --name-status --no-renames BASE...<branch> > /tmp/claude/split/name-status.txt`
- `git diff --no-renames BASE...<branch> > /tmp/claude/split/net.diff`
- `git diff --stat --no-renames BASE...<branch> > /tmp/claude/split/stat.txt`

`BASE...<branch>` (three dots) is the **net** diff against the merge base, so "a later commit modified
an earlier commit's file" is already collapsed. `--no-renames` is **mandatory**: a one-sided rename in
a path-filtered diff yields a patch that cannot apply. Dump to **`/tmp/claude/`, not the session
scratchpad** — `allow-tmp-access.sh` pre-approves commands matching `/tmp/claude/`, and the scratchpad
path is not in its regex.

**2b. Delegate to the slice planner subagent.** Read
`~/code/.claude/skills/split-pr/references/slice-planner-prompt.md` and hand its §1 prompt to the
subagent together with the three input paths above, the plan file and its per-step file lists, the
repo's `## Claude Config`, and `references/ci-gates.md`. Inject the WORKTREE DISCIPLINE block
(`~/code/CLAUDE.md`). That reference owns the whole analysis: generator-ownership with its worked
counter-examples, the atoms table and each atom's CI enforcer, the three straddler mechanisms in
priority order, the unrelated-changes bucket, the plan step-membership computation, and the output
schema. **Do not restate any of it here and do not re-derive it inline** — a second, paraphrased copy
is how the two drift apart. The planner is read-only over the repo plus one `Write`: it never cuts a
branch, applies a patch, or edits a tracked file.

**2c. Validate the planner's output before showing it to the user.** Each of these is **blocking** —
send it back to the planner with the specific defect; never patch the output silently:

- a file in `name-status.txt` missing from every part, or present in two parts without being declared
  under `HUNK-SPLIT SEAMS`;
- an atom spanning two parts (the reference's §2.4 makes this a blocking finding, not a note);
- a `rule`/`justification` cell left empty, or an ordering argument that asserts a layer convention
  instead of answering the reference's Rule 5 four mechanical questions;
- the unrelated-changes part carrying anything other than the literal `none` in `plan-steps`.

**2d. Present the split via `AskUserQuestion` — before building anything.** State: the parts in merge
order with file and line counts, the verified dependency arrow, each part's plan-step list, the
unrelated-changes part flagged as needing **its own issue**, and — explicitly, not as a footnote —
that there is **one staging deploy per *merged* part** (verified): `dev-build-and-deploy.yml` gates on
`merged == true` — a plain close only fires a `skipped` run — so N parts produce N deploys, and a
backend-only part sits live-but-unreachable on staging until its frontend lands. A deliberate trade
the user must see **here**, not discover after the first merge. Options: **accept** / **adjust the
boundaries** (re-run 2b with the correction) / **abort**. **Never cut a branch before acceptance.**

**2e. Write the accepted split to `<plan-dir>/split/split-manifest.md`** with the Write tool — a
durable record, because this skill spans N CI waits and N human merges and each `/loop` tick is a
fresh, context-free prompt. One row per part, in merge order, with exactly this schema:

| part | pathspec-file-path | branch | base-SHA | PR# | CI-status | merge-status | status | plan-steps |
| ---- | ------------------ | ------ | -------- | --- | --------- | ------------ | ------ | ---------- |

- **Every row starts at `status: planned`** here. `status` (`planned | built-and-verified | pushed |
  merged`) is deliberately **coarser** than `CI-status`/`merge-status`, which keep tracking live
  per-check and per-merge detail: it exists so that 1b's resume never has to *infer* which steps
  already ran. Skill Steps 4, 5 and 6 are the only writers of its later values.
- `plan-steps` holds the step-membership list verbatim (comma-separated step numbers, or the literal
  `none`); Skill Step 5 emits it unchanged in each PR's `Plan-Steps:` header.
- Write each part's `<part>.paths` pathspec file into the **durable** `<plan-dir>/split/` folder —
  **not** `tmp/`, and not `/tmp/claude/` — so a resumed run can re-locate it after a fresh context
  starts. `pathspec-file-path` records that path.

**2f. Sync to stronghold.** Once the manifest is written and the split accepted, run 1g's five-call
sequence again (`cd ~/code` → `git rev-parse HEAD` → `.claude/scripts/stronghold-sync.sh "<message>"`
→ `git rev-parse HEAD` and compare the two literal outputs as plain text → `cd ~/code/<repo-dir>`),
with the same stop-and-report rule when neither a changed SHA nor an explicit "nothing to commit"
holds.

### 3. Build the parts and prove the union invariant

Build every part at the **pinned literal `BASE`** and prove completeness **before** pushing anything.
**Read `references/invariant-checks.md` first** — it owns every command, the flag rationale, the
spurious-failure catalog, the mismatch protocol and a ledger correcting two assumptions inherited
here (3b, 3c). **Orchestrator-only, like Skill Step 5 — never delegate it:** 3c/3e's `cd`-then-bare
`git apply`/`git merge` need the Bash cwd to persist, and it does **not** in a subagent thread (§6.6),
where the `-C` fallback's prompt has nobody to answer. Nothing here touches `origin` and nothing
writes `status`: **every part stays `planned`**; only Skill Step 4 writes `built-and-verified`.

**3a. Partition check — FIRST.** `git diff --name-only --no-renames BASE...<branch> | LC_ALL=C sort`
must equal all `<part>.paths` concatenated and `LC_ALL=C sort`ed: `comm -3` prints nothing (nothing
lost or invented), `uniq -d` nothing but declared hunk-split seams. **`LC_ALL=C` on both is
mandatory** — `comm` needs byte order, a locale sort is not, and a mis-ordered `comm` is silently
wrong either way yet still prints nothing when all is well. Needing only `.paths`, it short-circuits
a doomed split in seconds. A failure is a **planner** defect: back to Skill Step 2. Reference §1.

**3b. One worktree per part pinned at `BASE`**, plus exactly one union worktree for 3e, so the primary
clone is never touched. **`git worktree add` is hook-DENIED in every sub-repo** — not allowlisted, and
a hook deny beats `Bash(git worktree:*)`; `git branch <name>` and `git checkout -b` likewise. Create
them through the repo's **own** worktree target with its base-ref override (app-main:
`make worktree-new name=… b=… from=BASE`); `make wt-new` cannot pin a base, and no override available
⇒ **STOP and report** — verifying at `origin/<default-branch>` instead of `BASE` breaks the invariant
by construction. Run `make -C ~/code wt-capacity REPO=<repo-dir>` first. Reference §2.

**3c. Build each part.** `git diff --binary --full-index --no-renames BASE...<branch> -- <literal
paths> > /tmp/claude/split/<part>.patch`, then — a **separate** Bash call, bare, from inside that
part's worktree — `git apply --index --whitespace=nowarn …`. **`git diff` has no
`--pathspec-from-file`** (verified: exit 129), so the `.paths` lines are spliced in literally. **Never
`--3way` on the first pass**: it can synthesize content and silently break the invariant. §3 also
records why `git checkout <head> -- <paths>` was rejected as the build primitive — no deletions.

**3d. Commit each part with its provenance trailers immediately after applying it** — before 3e, which
merges each part's *branch* and so needs every part already committed. One single-line `-m` plus
`--trailer` flags (`Original-Commit:`, `Co-Authored-By:`, `Claude-Session:`); the heredoc message form
is blocked. Reference §4.

**3e. Union invariant (authoritative).** Merge the parts in order into the union worktree with a plain
`git merge` — **never `--ff-only`**, which is structurally impossible past the first part because the
parts are independently rooted at `BASE` rather than stacked; it is safe because 3a already proved
disjointness, so every merge is trivial by construction and a conflict instead *falsifies* 3a. Then
`git rev-parse HEAD^{tree}` must equal `git rev-parse <branch>^{tree}`: tree objects hash paths,
contents and modes recursively, catching a missed file, a stray mode bit or content drift, where a
plain `git diff` would lie (rename detection, whitespace, diff algorithm, textconv). **Abort the whole
operation on mismatch** — never push a partial split. Reference §5-§7.

### 4. Per-part local green gate

Prove each **prefix** of the merge order green in the worktree Skill Step 3 built for that part — kept
until every part has passed — before spending a CI cycle and a human review. **Read
`references/ci-gates.md` before any gate:** it owns the gate tiers, the per-part recipes, the
which-gates-lie analysis, the marker derivation, the local ↔ CI job map, the cleanup forms and a ledger
correcting **seven** assumptions inherited here.

- **4a. Gate the PREFIX, then initialize the worktree — both before any gate.** Merge part branches
  *1…k-1* into part *k*'s worktree first (invariant 2 is *prefix* greenness, and part *k*'s real branch
  is cut **after** they merged, so a part that consumes an earlier part's artifact is red alone at
  `BASE` **by construction** — verified, `tsc` TS2344). Then init that worktree **once**: dep install
  plus one stack start (app-main: `make tools`, then `make up d=1`). `worktree-new` links `.env` only;
  skip either and tier 0 dies on a missing toolchain while a "stackless" container gate run first
  **poisons** the worktree's cached images. §0, §1, §6d-6, §6d-7.
- **4b.** Repo **`Commands`** seam only: `make -C <worktree> <target>`, never raw; output to
  `/tmp/claude/<name>.txt`, read from the file. Cheapest tier first, `-built` **last** and once; derive
  the marker set with the repo's own diff-scoped selector, not by hand. §1, §3, §6.
- **4c.** **One built/full-stack run per part is non-negotiable** — the cheap gates **lie** wherever the
  repo's test setup mocks the seam the split cuts. §2-§3, §6b, §6d.
- **4d.** **A local failure STOPS the run.** Never re-assign files between parts to make a gate pass:
  that changes the manifest the user accepted. §0.
- **4e.** Write **`built-and-verified`** to the manifest `status` column **per part, here and only here**,
  after that part's built run — Skill Step 3 wrote nothing. Skill Step 1b's resume reads it. §4.
- **4f.** Only then clean up: every verification worktree **and its live stack**, its throwaway branch,
  the ephemeral `/tmp/claude/split/*.patch` — **never** `<plan-dir>/split/<part>.paths`. `down` a green
  part's stack between parts. §5.

### 5. The sequential push → PR → CI → merge loop

Ship the parts **one at a time**, each on a branch cut fresh from the then-current
`origin/<default-branch>`. **Read `references/pr-loop.md` first** — it owns every exact command, the
manifest writes a resume depends on, and a verified-facts ledger correcting four assumptions inherited
here (5a, 5c, 5g, 5d's GraphQL reuse). **Orchestrator-only — never delegate this step:**
`push-gate.sh`, `<bot-push-script>` and `pr-finish.sh` resolve their target from the Bash cwd, which
does not persist in a subagent.

- **5a. Cut part *k*'s branch from a freshly-fetched `origin/<default-branch>`** via
  `make wt-new REPO=<repo-dir> BRANCH=<part-branch>` from `~/code` — **`git checkout -b` is
  hook-DENIED** here (allowed only on a default branch that is *ahead* of origin), and `wt.sh` does not
  fetch for you. Cutting **fresh per part**, rather than pre-cutting all N branches, is what keeps
  `push-gate.sh`'s `git cherry` staleness guard satisfied after each squash-merge. §2.
- **5b. Rebuild part *k*'s patch — a fresh rebuild, not a reuse** (Skill Step 4's cleanup deleted the
  verification patches; only `<part>.paths` survived). Read part *k*'s **literal** `base-SHA` from the
  manifest — recorded once in 1d, never recomputed here, never a shell variable — hard-code it into the
  diff, and splice the `.paths` lines in literally after `--` (**`git diff` has no
  `--pathspec-from-file`**: exit 129). Apply **bare** inside the part's worktree, then commit with one
  single-line `-m` plus repeated **`--trailer`** flags (`Original-Commit:` listing the squashed SHAs,
  plus `Co-Authored-By:` and `Claude-Session:`) — a heredoc message is blocked, and repeated `-m` would
  split the trailer block into paragraphs. Delete `/tmp/claude/split/<part>.patch` afterwards. §3.
- **5c. Gate, then push.** `~/code/.claude/scripts/push-gate.sh <part-branch>` is a **hard
  precondition, not `/git-push`'s business**: `<bot-push-script>` exits 6 without a passing result for
  the exact HEAD sha, and `require-push-gate.sh` refuses the call. `git push` runs as **its own Bash call
  with nothing chained or piped** (rule `ops: all`, so even a pipe is denied). The bot script needs **no**
  `dangerouslyDisableSandbox` — it is sandbox-excluded; only the `Bot identity: n/a` SSH fallback does. §4.
- **5d. Open the PR** with the part's scope in title and body, a link to the original PR, a
  `Split from #<original>` line, and a **`Plan-Steps:`** header carrying the manifest's `plan-steps`
  cell **verbatim** — a comma-separated list of step numbers, never a contiguous `A-B of N` range, and
  the literal `Plan-Steps: none (unrelated changes)` for the unrelated part. **That is the header plan
  Step 7's companion `/pr-review` change will parse** to scope Plan Conformance. Compose the body with
  the Write tool and pass `--body-file` (a heredoc body is blocked); board, assignee and the **PR
  reviewer** seam's reviewer come from `pr-finish.sh`, which already reuses `git-push`'s
  `references/graphql/*.graphql` — never re-author that GraphQL inline. §5.
- **5e. Issue wiring:** only the **last** part in merge order carries `Closes #<issue>`; every earlier
  part carries `Part of #<issue>`; the unrelated-changes part gets its **own** issue, since it is not
  part of the original work. The trap: `/git-push` appends `Closes` automatically, so reused naively all
  parts close the same issue and the *first* merge closes it prematurely. §5.
- **5f. Write `pushed` into the manifest's `status` column** the moment the branch is pushed and the PR
  is open — the write 1b's resume reads to skip 5a-5e and re-enter at 5g. §5-§6.
- **5g. Wait for CI** with `GH_TOKEN=$(<token-generator>) gh pr checks <PR#>`, polled by the **Monitor
  tool or `/loop` dynamic mode — never a shell poll loop**. Fetch failure logs through
  `gh-log-fetch.sh`, which exists to survive the sandbox's `~/.cache/gh` denial — run it from `~/code`
  with a **mandatory** `--repo <Repo slug>`, prefixed `GH_TOKEN_GENERATOR=<token-generator>` only when
  the seam differs from the wrapper's own resolution order. §7.
- **5h. Hand back to the user for the merge** — merging is the human's call; the skill resumes from the
  manifest. Then **confirm the merge programmatically** (`gh pr view --json state,mergedAt`, or
  `git cherry origin/<default-branch> <part-branch>` as fallback) and only then write `merged` into
  **both** `merge-status` and `status`: a human's "merged" in chat is **not** evidence. Re-fetch, cut
  part *k+1*, and **never chain it off part *k*'s branch** — the squash-merge makes those commits stale
  duplicates the gate hard-blocks, and a PR based on a part branch runs zero checks. §8.
- **5i. Sync after every part**, once that merge is confirmed: 1g's five-call sequence, same
  stop-and-report rule when neither a changed SHA nor an explicit "nothing to commit" holds. §9.

### 6. Disposition, and the downstream skills that assume one PR

Runs only once **every** part shows `merged` in the manifest, programmatically confirmed (5h). **Read
`references/disposition.md` first** — every exact command, the irreversibility gates, and a ledger
correcting **seven** assumptions inherited here. **Orchestrator-only, like Steps 3 and 5:** every call
resolves its target from the Bash cwd, which does **not** persist in a subagent; else **STOP and report**.

- **6a. Cross-link, then close the original — never `--delete-branch`** (`-d` deletes the branch before
  6b's archive re-check). `gh pr close` takes **only** `-c, --comment` — **no `--body-file`** — so the
  multi-line cross-link goes through a separate `gh-bot.sh pr comment … --body-file` (Write-tool body).
  Closing late keeps the review-thread anchoring and the green-CI evidence alive. §1.
- **6b. Delete the branch behind an archive re-check *and your own* `AskUserQuestion`** — `test -s` all
  five `<plan-dir>/split/` artifacts, then **gate it yourself: the harness will not prompt**
  (`allow-git-ops.sh` only *defers* `branch:-D`; `Bash(git branch:*)` then allows it). Clear the branch's
  worktree (`make wt-rm`) or git refuses; `git branch -D` (`-d` fails "not fully merged"), then
  `gh-bot.sh api -X DELETE repos/<Repo slug>/git/refs/heads/<branch>` — never `git push --delete`. §2.
- **6c. Record, never rewrite.** Leave the plan's checkboxes and `branch:` alone; add `split_into:` below
  the `github_issue*` keys, close out the manifest, then **sync** via 1g's five-call sequence. §3-§4.
- **6d. Warn about the one-PR assumptions** when handing back: parts go strictly **one at a time** (one
  fixed-name topic `tmp/`, shared and wiped); `/pr-review` usually lands on a picker where **"Skip plan
  conformance"** is right, and flags every step missed if a plan *is* resolved; `/git-push`'s
  plan-`branch:` prompt gets **"push anyway and update `branch:`"**. Fix: a tracked companion. §5-§6.

## References

Read one level deep, on demand. All six live in `references/` beside this file; the first five end
with a verified-facts ledger (**LIVE** rows came from the real rehearsal).

- `slice-planner-prompt.md` — the full planner subagent prompt: input format, the generator-ownership
  rule with counter-examples, the atoms table, the inert-superset and amend-in-later-part rules, the
  unrelated-changes bucket, the output schema. **Step 2b**: hand its §1 prompt to the planner.
- `invariant-checks.md` — the exact partition, worktree, build, commit and tree-hash sequences with
  flag-by-flag rationale; the spurious-failure catalog (moving base, rename detection, mode bits,
  CRLF/`.gitattributes`, submodule gitlinks, wrong-tree applies); the mismatch protocol. **Step 3.**
- `ci-gates.md` — the per-repo "what fails on a partial change" catalog: the prefix rule, one-time
  worktree init, gate tiers, per-part recipes, which cheap gates lie, marker derivation, the local ↔ CI
  job map, the `status` write, the cleanup forms. Stack specifics tagged. **Step 4**, before any gate.
- `pr-loop.md` — the per-part push → PR → CI → merge procedure: branch creation (`make wt-new`, not
  `git checkout -b`), the patch rebuild and trailer commit, push gate and push shapes, the PR body
  contract and `pr-finish.sh`, the CI wait and log fetch, the merge confirmation and what the merge
  does on its own (auto-deleted remote ref, one staging deploy), the manifest writes. **Step 5.**
- `disposition.md` — the close + cross-link calls and `gh pr close`'s missing body-file flag, the
  archive re-check and self-gated branch deletion, which branch still needs an explicit remote delete,
  `split_into:`, the sync, and the one-PR assumptions downstream with the right answer. **Step 6.**
- `pre-pr-mode.md` — the `/git-push` Step 7e handoff: the push-gate entry check that replaces green
  CI, why no re-review is needed, and the per-step deltas. **Read first when `--pre-pr` is passed.**

## Status

**Fully verified.** Steps 1-3 by plan 9a/9b, Step 4 by 9c, Steps 5-6 by 9d's **live** rehearsal (PRs
#815/#816: real push gate, bot push, `pr-finish.sh`, 36 green checks, a real merge, then close +
cross-link + branch deletion). Standing caveats, all by design: **the merge is always the human's**
(both `gh-bot.sh pr merge` spellings are harness-denied); **Steps 3, 5 and 6 are orchestrator-only**
(no persistent subagent cwd).
