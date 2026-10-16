---
name: gmas
description: Run the `gmas` workflow — checkout main, pull, fetch -p, and interactively clean up stale local branches. Use when the user says "run gmas", "/gmas", "update main and clean branches", "clean up stale branches", or similar. Presents the candidate list as a multiSelect AskUserQuestion so the user picks which branches to delete (not all-or-nothing).
---

# gmas — Update main and clean orphaned local branches

Mirrors the user's `gmas` shell alias (`git checkout main; git pull origin main; git fetch -p; git cleanup`) but routes the cleanup confirmation through `AskUserQuestion` so the user picks individual branches to delete instead of a single blanket yes/no.

**Repo-agnostic.** This skill hardcodes no repo slug, branch name, or stack detail — it operates on whatever repo is the current working directory, using its `origin` remote. The only cross-repo assumptions are (1) the stronghold's sandbox-protected working-tree path list (`.claude/settings*.json`, `.claude/hooks/**`, `.claude/skills/**`, `.mcp.json`), which is the same for every sub-repo worked on from the stronghold, and (2) the user-global `git cleanup` alias in `~/.gitconfig`, which is a one-line pointer at the tracked `.claude/scripts/git-cleanup.sh` (`cleanup = !~/code/.claude/scripts/git-cleanup.sh`). If `git cleanup` reports `'cleanup' is not a git command`, this machine is missing that alias — register it rather than hand-rolling the candidate list. The default branch is assumed `main`; if a repo's `## Claude Config` declares a different **Default branch**, substitute it for `main` throughout.

## Workflow

### 0. Detect a linked worktree

Run these as two separate Bash calls and compare the output:

```
git rev-parse --absolute-git-dir
git rev-parse --path-format=absolute --git-common-dir
```

If they differ, you are in a **linked worktree** (a checkout under `<repo>/.claude/worktrees/<slug>`); if they match, you are in the primary checkout. Remember which for Steps 2 and 6.

### 1. Pre-flight safety

Run `git status --porcelain`. If output is non-empty, stop and use `AskUserQuestion` to offer:

- Commit changes first (delegate to `/git-commit`)
- Stash changes (`git stash push -u -m "gmas pre-flight"`)
- Abort

Do NOT silently stash, reset, or force-checkout.

### 2. Refresh main

**In a linked worktree:** run only `git fetch -p`. Skip the two leg diffs, `git checkout main`, `git pull origin main` and the protected-path DDS gate below — `main` is checked out in the primary, so no working-tree pull happens here. Record "primary's `main` was not pulled (ran from a worktree)" for the Step 6 report. From the primary checkout, behavior is unchanged: run the sequence below.

From the primary checkout only, run as separate Bash calls, in this order:

```
git fetch -p                            # in-sandbox — prunes + updates origin/main
git diff --name-only HEAD main          # in-sandbox — read-only; the CHECKOUT leg (HEAD → local main)
git diff --name-only main origin/main   # in-sandbox — read-only; the PULL leg (local main → origin/main)
git checkout main                       # in-sandbox by default; DDS only if protected-path hit (see below)
git pull origin main                    # in-sandbox by default; DDS only if protected-path hit (see below)
```

**Why the diff gate.** The sandbox writes git internals (`.git/objects`, `.git/refs`) fine — `git fetch` and `git pull` of ordinary code changes run in-sandbox with no prompt. The sandbox only denies a narrow set of **working-tree paths**: `.claude/settings.json`, `.claude/settings.local.json`, `.claude/hooks/**`, `.claude/skills/**`, `.mcp.json` (plus `.git/config`). A checkout/pull that rewrites one of those partially fails and **tears the working tree** (HEAD moves but blocked files keep old content → phantom "local changes" on every later git command). `dangerouslyDisableSandbox: true` swaps the tree atomically, but it forces a per-call confirmation that can't be allowlisted — so apply it **only when actually needed**.

**Check both legs separately — never the net `HEAD…origin/main` diff.** The two commands rewrite the working tree in two distinct hops, and the net endpoint diff can hide a protected-path write that the intermediate hop performs:
- `git checkout main` rewrites every file that differs between **HEAD and local `main`** → its risk is `git diff --name-only HEAD main`.
- `git pull origin main` (fast-forward) rewrites every file that differs between **local `main` and `origin/main`** → its risk is `git diff --name-only main origin/main`.

A single `HEAD…origin/main` diff compares only the endpoints, which **cancel out** in the common squash-merge case: you're on a just-merged feature branch whose tree already holds the new `.claude/**` content (you authored it there), local `main` is stale with the *old* content, and `origin/main` has the new content. Then `HEAD` and `origin/main` match (empty diff → guard says "in-sandbox"), but `checkout` still reverts the protected files HEAD(new) → main(old) — a blocked write that tears the tree. Checking the checkout leg (`HEAD vs main`) catches this; the endpoint diff does not.

**Decision rule** (after the two leg diffs):
- If **either** diff has any line matching `.claude/settings.json`, `.claude/settings.local.json`, `.claude/hooks/`, `.claude/skills/`, or `.mcp.json` → run **both** `git checkout main` and `git pull origin main` with `dangerouslyDisableSandbox: true` (a sandbox-protected path is rewritten by at least one leg; the one confirmation is warranted — a human should glance at a hooks/skills/settings change anyway).
- Otherwise → run both **in-sandbox** (no DDS). `git checkout` is allowlisted; `git pull origin main` is allowlisted via `Bash(git pull origin main:*)`, so neither prompts.

**Fallback (primary-checkout path only).** If an in-sandbox `git checkout`/`git pull` unexpectedly fails with `Operation not permitted` (a protected path the glob list missed), retry that single command with `dangerouslyDisableSandbox: true`, then continue. **If the tree is already torn** — `git checkout` printed `unable to unlink old '<protected path>'` but still reported `Switched to branch 'main'`, so HEAD moved while the protected files kept old content — a plain retry won't recover: the protected files now read as phantom local changes and the follow-up `git pull` aborts with `Your local changes ... would be overwritten`. Since those changes are already merged into `origin/main`, recover by atomically resyncing: `git reset --hard origin/main` with `dangerouslyDisableSandbox: true`. This is lossless **only** when the working tree was clean at Step 1 (gmas guarantees this) and the only "modifications" are the merged protected files — verify with `git status --porcelain` showing nothing but those paths before resetting.

If `git pull` (primary-checkout path) reports a non-fast-forward, merge conflict, or other error, surface it verbatim and stop. Do not attempt to resolve automatically.

### 3. Preview cleanup candidates

Run:

```
echo n | git cleanup
```

This invokes the alias with `n` piped to its confirmation prompt, so the candidate list is printed but nothing is deleted. Parse the lines between `Will delete:` and `Proceed?` — each candidate is a single branch name indented by two spaces.

The output may also start with an optional `Merged worktrees:` block, printed **before** `Will delete:` / `No branches to clean up.`. Parse it separately from the branch list: each line up to `Will delete:` or `No branches to clean up.` is `  <worktree path>  <branch>` (two-space indent, path, two spaces, branch); split each line on its **last** two-space gap, since branch names cannot contain spaces but a path might. These are linked worktrees whose branch's upstream is gone (merged). The script never removes them and never counts their branches as candidates; branches checked out in any worktree are already excluded from the `Will delete:` list.

If the output is `No branches to clean up.` AND no `Merged worktrees:` block was present, report that and stop. Otherwise continue to Step 3b even when there are no ordinary candidates.

### 3b. Remove merged worktrees

Skip this step if no `Merged worktrees:` block was listed. Do it before Step 4 so a freshly freed branch shows up as a candidate in the same run.

Use **`AskUserQuestion` with `multiSelect: true`**: each option's label is the worktree path's basename, and its description is `<branch> — upstream gone (merged)`. Apply the same batching rules as Step 4 (4 options per question, 4 questions per call). Removal goes through the central dispatcher `wt.sh rm`, which delegates to the repo's own teardown (or the generic seam-driven one) so volumes, images and DB are reclaimed along with the checkout.

1. For each selected worktree, capture `<repo>` (the basename of the path segment before `/.claude/worktrees/` in its `Merged worktrees:` line) and `<branch>` (the last column).
2. Resolve the absolute stronghold path at runtime, once. Run this as its own standalone Bash call:

   ```
   git rev-parse --path-format=absolute --git-common-dir
   ```

   Read its plain-text output (`<stronghold>/<repo>/.git`), strip the last two path components (`.git` and the repo directory) yourself, and keep the resulting literal absolute path as `<resolved-absolute-stronghold-path>`. Never use `dirname $(...)`, `$(...)` or backticks to do the strip (the command-substitution guard blocks them).
3. For each selected worktree, run this as its own standalone Bash call, using the literal path from step 2 — no `cd`, no chaining, no piping:

   ```
   <resolved-absolute-stronghold-path>/.claude/scripts/wt.sh rm <repo> <branch>
   ```

   This path is textually identical to the sandbox-excluded `__STRONGHOLD__` entry, so it runs without a prompt and gmas's cwd stays the sub-repo (the rerun `echo n | git cleanup` below depends on that). Never substitute a static `~/code/...` literal for the resolved path.

If a removal fails (e.g. a dirty tree, or a repo with no worktree support), report the refusal text verbatim and do **not** retry with `--force` or work around it with a raw `git worktree remove` — the sandbox hook defers `--force` to a prompt, and the user can run it themselves. A worktree cannot remove the tree it is running in, which is why the script never lists the current one; likewise it never lists locked worktrees (a non-force remove fails on them).

If no worktree was removed (the user selected none, or every removal failed): if Step 3 printed `No branches to clean up.`, go to Step 6 and report; if it listed candidates, Step 4 uses the Step 3 `Will delete:` list.

If any worktrees were removed, rerun `echo n | git cleanup` so the freed branches become ordinary candidates. **Step 4 must then select from this rerun's `Will delete:` list, not the original Step 3 preview.** If the rerun prints `No branches to clean up.`, go straight to Step 6 (report the removed worktrees).

### 4. Let the user pick which branches to delete

Use **`AskUserQuestion` with `multiSelect: true`**. Each option's label is the branch name; the description is a short reason — e.g. `"no upstream + missing on origin"` (orphan) or `"upstream pruned (: gone])"` (classic gone branch). Determine each branch's reason by checking whether `git for-each-ref --format='%(upstream)' refs/heads/<branch>` returns an upstream — empty means orphan, non-empty means gone.

**Batching rules (AskUserQuestion caps: 4 options per question, 4 questions per call → 16 candidates per call):**

- **1–4 candidates:** one question, one option per branch.
- **5–16 candidates:** multiple questions in a single call (up to 4 questions × 4 options each). Group sensibly — e.g., one question per "reason" category if that makes the choice clearer.
- **17+ candidates:** sequential `AskUserQuestion` calls, 16 at a time.

For each question, phrase like `"Delete these stale branches?"` with multiSelect on. The user checks the ones to delete; unchecked branches stay.

### 5. Delete selected branches

Run a single in-sandbox Bash call:

```
git branch -D <selected1> <selected2> ...
```

Skip any branches the user did not check.

### 5b. Close issues left open by finished plans

Merges are what close most issues, but an umbrella or a multi-PR plan can leave one open. Run `~/code/.claude/scripts/plan-lifecycle.sh close-issues <repo-dir>` as its own standalone Bash call (`<repo-dir>` = basename of the primary checkout; from a linked worktree, the path segment before `/.claude/worktrees/`). It is deterministic and safe to run unconditionally: it closes only the open issue of a plan already in `completed/`, and leaves any issue still named by a plan in `open/` or by an open PR's closing references. Exit 4 (no `Plans bucket`, or `gh` unavailable) is non-fatal here: note it in the report and continue.

### 6. Report

One-line summary:

- `Deleted N branches: foo, bar, baz. Kept M: qux.`
- Or `No branches deleted.` if the user checked nothing.

Also include, when they apply: `Closed issues: #N, #M` (from Step 5b), `Removed N merged worktrees: <paths>` (removed via `wt.sh rm`, plus any removal that failed with its refusal text), and — when run from a linked worktree — "primary's `main` was not pulled (ran from a worktree)".

## Notes

- The `git cleanup` alias lives in `~/.gitconfig` and excludes `main` by design. Its logic is the tracked `.claude/scripts/git-cleanup.sh`, so it travels with the stronghold — the alias itself is only `cleanup = !~/code/.claude/scripts/git-cleanup.sh`. A candidate is a branch that is neither the default nor the current branch and either has a gone upstream or has no upstream and no `refs/remotes/origin/<branch>`. That second category also matches a never-pushed local branch, which is why the per-branch `AskUserQuestion` in Step 4 is not optional.
- `git cleanup` prunes stale worktree records (`git worktree prune`) and never offers a branch that is checked out in a worktree; it only reports merged worktrees (`Merged worktrees:`) and never removes one. gmas Step 3b removes them by invoking `wt.sh rm <repo> <branch>` directly (repo-owned or generic teardown) through the runtime-resolved absolute stronghold path — not `make wt-rm`, which stays the manual/human entry point for the same script.
- Worktree Discipline cross-reference: see `~/code/CLAUDE.md` → Worktree Discipline for delegated subagents (canonical). The Branch Guards of other skills (`plan-creator`, `git-commit`, `git-push`, `plan-reviewer`, `run-plan`, `next-step-taker`) no longer invoke `gmas` — switching the shared primary clone to `main` is exactly what that discipline removes — so `gmas` is now only run by the user (or from a finished branch/worktree) as the post-merge cleanup step.
- Never pipe `y` directly into `git cleanup` — always go through `AskUserQuestion`. The pipe is only for preview (`n`).
- `git checkout main` and `git pull origin main` (Step 2) run **in-sandbox by default** — git internals (`.git/objects`, `.git/refs`) are sandbox-writable. They escalate to `dangerouslyDisableSandbox: true` **only** when **either** leg diff — `git diff --name-only HEAD main` (checkout) or `git diff --name-only main origin/main` (pull) — shows a sandbox-protected working-tree path (`.claude/settings*.json`, `.claude/hooks/**`, `.claude/skills/**`, `.mcp.json`) being rewritten, which would otherwise tear the tree. **Do not use the net `HEAD…origin/main` diff** — it cancels out the protected-path write that the checkout leg performs when you're on a just-merged branch that carried `.claude/**` changes (the exact case that tears the tree). `git branch -D` (Step 5) may print a `could not write config file .git/config: Operation not permitted` warning under sandbox — the branch is still deleted, so it's harmless; no DDS needed. All other steps run in-sandbox.
- Branches with unique commits not on main are force-deleted via `-D` — acceptable because the user has explicitly opted in per branch.
