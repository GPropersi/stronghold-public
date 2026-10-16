# Disposition — closing the original, deleting its branch, and the one-PR assumptions downstream

The procedure behind **Skill Step 6** of `/split-pr`. Read this in full before running any of it: this
is the only step in the skill whose actions are **irreversible** (a closed PR can be reopened, but a
deleted branch and its orphaned review-thread anchoring cannot be restored), and the only step whose
output other skills will misread unless the operator is told in advance what they will see.

Everything here runs **after the last part has merged** and from inside the sub-repo checkout
`~/code/<repo-dir>`, except §4's stronghold sync and §2c's `make wt-rm` (both from `~/code`).
Placeholders (`<default-branch>`, `<token-generator>`, `<Repo slug>`, `<plan-dir>`, `<repo-dir>`)
carry SKILL.md's meanings.

**Skill Step 6 is orchestrator-only — never delegate it to a subagent.** Every call here resolves its
target from the **Bash cwd**, not from a flag: `gh-bot.sh` picks the App installation from the cwd's
`origin` (§1, §2d), `git worktree list` / `git branch -D` act on the cwd repo (§2c, §2d), and
`make wt-rm` / `stronghold-sync.sh` must run from `~/code` (§2c, §4). **A subagent thread's cwd does
not persist between Bash calls** — it resets to the project directory, verified directly (`cd
~/code/<repo-dir>` then `pwd` prints `~/code`) — so a delegated Step 6 runs every one of those calls
against the **stronghold** instead of the sub-repo, and `cd … && …` is denied by
`block-compound-commands.sh`. This is the same rule Skill Steps 3 and 5 carry (`pr-loop.md` §0), for
the same reason, and it applies to the *whole* step including its read-only `test -s` re-checks. If
the cwd cannot be set to the sub-repo checkout, **STOP and report** — do not substitute a `-C` form,
do not newline-separate a `cd`, and never guess at the target.

---

## 0. Entry precondition — all N parts `merged`, verified from the manifest

Do **nothing** in this step until `<plan-dir>/split/split-manifest.md` shows `merged` in **both**
`merge-status` and `status` for **every** part, each written only after Skill Step 5h's programmatic
confirmation (`gh pr view --json state,mergedAt`, or `git cherry origin/<default-branch>` as fallback).
A human's "they're all merged" in chat is **not** evidence — the same rule that governs 5h.

If any part is still `planned`, `built-and-verified` or `pushed`, go back to Skill Step 5 for that
part. Closing the original early throws away the live review threads and the green-CI evidence that
are the fallback if a later part turns out to be mis-sliced.

---

## 1. Cross-link, then close the original PR — **without** `--delete-branch`

**Why "only after the last part merges."** The original PR is the only place that still holds, live and
anchored, (a) every existing review thread at its original (SHA, path, line) anchors and (b) the green
check-run evidence that justified the split in the first place. `<plan-dir>/split/review-threads.md`
is a *text export*, not a restoration path: GitHub cannot re-anchor threads once the head ref is gone.
Keeping the original open costs nothing and keeps that evidence one click away for every part review.

**Why the wrapper, not the bare `gh` form.** `GH_TOKEN=$(<token-generator>) gh pr close` would work
authentication-wise, but the comment text here is the cross-link body — multi-line, and full of
backticks and `#`-references. Any `{` or quote in a Bash command line trips Claude Code's built-in
brace+quote security prompt (the very prompt `block-bash-writes.sh:22` exists to steer around), so a
rich comment passed inline is a prompt waiting to happen. The wrapper keeps the token off the command
line, and §1's file-based body keeps the text off it too.

**Spell the wrapper absolutely.** `~/code/.claude/scripts/gh-bot.sh` — allowlisted as
`Bash(~/code/.claude/scripts/gh-bot.sh:*)` and the only spelling that *resolves* from a sub-repo cwd
(`gh-bot.sh` exists only in the stronghold; no sub-repo carries a copy). The relative spellings in the
other allow entries are there for stronghold-cwd calls and for `workflow-lint`'s both-forms coverage —
they are not runnable from `~/code/<repo-dir>`. Still run it **from inside the sub-repo**: the App
installation is resolved from the cwd's `origin`, never from `--repo` (`gh-bot.sh:37-45`).

**What it looks like when you get the cwd wrong — verified live in plan 9d.** `gh-bot.sh pr comment
816 --body-file …` run from `~/code` (the stronghold, `origin` = `OWNER/stronghold`) failed with

```
GraphQL: Could not resolve to a PullRequest with the number of 816. (repository.pullRequest)
```

**even though the call carried `--repo <Repo slug>`.** The identical command from `~/code/app-main`
succeeded. The error names the *PR number*, so it reads like a wrong or closed PR — it is neither. Any
"could not resolve to a PullRequest/Repository" from a `gh-bot.sh` call means **the cwd is wrong**:
`cd` into `~/code/<repo-dir>` as its own Bash call and re-run the command unchanged. Do not "fix" it by
re-checking the PR number, adding `--repo`, or switching to a bare `gh` form.

### 1a. `gh pr close` has **no** body-file flag — this is the trap

Verified against the installed `gh`:

| Command         | Body flags it accepts                                               |
| --------------- | ------------------------------------------------------------------- |
| `gh pr close`   | `-c, --comment string` **only** — no `--body-file`, no `-F`         |
| `gh pr comment` | `-b, --body text` **and** `-F, --body-file file` (also `-` = stdin) |

So the multi-line cross-link comment **cannot** go through `pr close`. Do not invent a `--body-file`
flag for it. Use two calls, one Bash call each:

1. **Compose the body with the Write tool** into `/tmp/claude/split/close-comment.md`
   (`block-bash-writes.sh` blocks `cat >`, `cat <<EOF`, `tee`, `echo >`, `printf >`; `allow-tmp-access.sh`
   pre-approves `/tmp/claude/`). Content: one line per child part, in merge order — part number, scope,
   `#<part-PR#>` and its full URL — plus the pinned `BASE`, the original head SHA, and a pointer to
   `<plan-dir>/split/` for the archive.
2. `~/code/.claude/scripts/gh-bot.sh pr comment <original-PR#> --repo <Repo slug> --body-file /tmp/claude/split/close-comment.md`
3. `~/code/.claude/scripts/gh-bot.sh pr close <original-PR#> --repo <Repo slug> --comment "Split into N sequential PRs - see the comment above"`

The closing `--comment` on (3) is a **single short line with no backticks, no braces and no newlines**,
so it stays prompt-free; all the substance lives in (2). Passing no `--comment` at all is equally fine.
Delete `/tmp/claude/split/close-comment.md` afterwards.

`--repo <Repo slug>` is explicit for legibility; the cwd is what picks the App installation, so the
flag is redundant but harmless — and it is **not** a substitute for being in the sub-repo.

### 1b. **Never** pass `--delete-branch` to the close

`gh pr close -d` deletes the local *and* remote branch as a side effect of the close — it would collapse
§1 and §2 into one unconditional action and skip §2's archive precondition entirely. Branch deletion is
a separate, separately-gated step. If `-d` were used, the deletion would happen **before** anyone
re-verified the archive, which is exactly the failure §2 exists to prevent.

### 1c. Cross-link **back** from each child

Each part PR's body already carries `Split from #<original-PR#>` (Skill Step 5d / `pr-loop.md` §5), so
the backlink exists from the moment each part opens. After the close, the links are symmetric:
original → all N children in §1a's comment, each child → original in its body. Do not rewrite merged
part bodies to add sibling links; GitHub already renders the whole cluster from the two directions.

---

## 2. Delete the original branch — behind an archive re-check **and an explicit confirmation**

### 2a. Re-verify the provenance archive first

Once `<branch>` is gone from both the local repo and `origin`, `<plan-dir>/split/` is the **only**
durable record of what the original was. Re-run Skill Step 1f's non-empty check — not a glance at a
directory listing — on every artifact, one `test -s` per file:

| Artifact                                 | What is unrecoverable without it                                        |
| ---------------------------------------- | ----------------------------------------------------------------------- |
| `<plan-dir>/split/original-series.patch` | the original commit series (`git format-patch --stdout BASE..<branch>`) |
| `<plan-dir>/split/original-log.txt`      | original authorship and trailers (`git log --format=fuller`)            |
| `<plan-dir>/split/review-threads.md`     | the exported review threads, resolved ones included                     |
| `<plan-dir>/split/original-pr.md`        | the PR number, head SHA, pinned literal `BASE`, green check-run IDs     |
| `<plan-dir>/split/split-manifest.md`     | the part→branch→PR→base mapping and every `merged` confirmation         |

Any one empty or missing → **stop and report**. Do not regenerate it from the branch and then delete
the branch in the same breath; a regenerated artifact has not been reviewed, and the point of the
precondition is that the archive was verified *before* anything became irreversible.

### 2b. **The harness will not prompt here — the skill must.** (Corrects the tracking plan.)

The plan asserted that `git branch -D` "defers to an interactive confirmation prompt even though
`Bash(git branch:*)` is otherwise allowlisted." **That is backwards, and verified false — first by
reading the hooks, then confirmed live in plan 9d's rehearsal**, where deleting two throwaway branches
in one `git branch -D` produced **no confirmation prompt at all**:

- `allow-git-ops.sh:83-94` detects `branch:-D` as a destructive variant and **exits 0 — it defers**.
  Deferring means "let the default permission system decide," not "force a prompt"
  (`allow-git-ops.sh:13-16`).
- The default permission system then matches `Bash(git branch:*)` (`settings.json:80`), which **allows**
  `git branch -D <branch>`.
- `block-branch-switching.sh` does not catch it either: `branch_guard.py`'s `BRANCH_MODE_SHORT`
  includes `D`, so `-D` is classified as delete-mode, never as a branch creation. (Bare
  `git branch <name>` *is* denied — verified by running it.)

Net effect: **an irreversible branch deletion runs silently, prompt-free.** So this step supplies its
own gate: before the two deletion calls, put the facts in front of the user with **`AskUserQuestion`**
(per `~/code/CLAUDE.md` → Workflow Rules, two-plus discrete choices never a plain-text `[y/n]`) —
naming the branch, the N merged part PRs that replace it, and the verified archive path — with options
to delete now or leave the branch in place. Treat a "leave it" answer as a complete, successful run:
the split is finished either way; only the tidy-up is deferred.

### 2c. The original branch's **worktree** blocks the deletion

If the plan carries a `worktree:` frontmatter value, or `git worktree list --porcelain` shows
`<branch>` checked out anywhere, git refuses the delete outright (`cannot delete branch '<branch>' used
by worktree at …`) — and `-D` does not override that; `--force` is about merge status, not checkouts.
Check first:

```
git worktree list --porcelain
```

If `<branch>` appears, remove its worktree through the sanctioned path **from `~/code`, as its own
Bash call** — `make wt-rm REPO=<repo-dir> BRANCH=<branch>` — never `git worktree add/remove` by hand
for a sub-repo worktree (`wt.sh` runs the repo's teardown and refuses a primary-checkout branch,
`wt.sh:413`). Then `cd ~/code/<repo-dir>` again before the deletions. The same applies if `<branch>`
is the **primary** checkout's current branch: `git checkout <default-branch>` there first (switching
*to* the default branch is the one move `block-branch-switching.sh` permits).

### 2d. Local and remote are two separate calls, and the remote one is an API call

One Bash call each, nothing chained:

```
git branch -D <branch>
~/code/.claude/scripts/gh-bot.sh api -X DELETE repos/<Repo slug>/git/refs/heads/<branch>
```

- **`-D`, not `-d`.** Every part was built by `git diff BASE...<branch> | git apply` into a fresh
  branch cut from `origin/<default-branch>` — the original branch's commits were never *merged*
  anywhere, so `git branch -d` fails `error: the branch '<branch>' is not fully merged`. The content
  shipped; the commits did not. `-D` is correct, not a shortcut.
- **`git push origin --delete <branch>` is hook-blocked** on an app-push repo:
  `block-unsafe-push.sh:229-236` blocks any bare `git push` that goes through neither the declared
  `<bot-push-script>` nor an `x-access-token:` URL, and a ref-deleting push is a bare push. The REST
  ref deletion through the App token is the supported path, and the relative spelling has its own
  allow entry (`Bash(.claude/scripts/gh-bot.sh api -X DELETE repos/*)`) for stronghold-cwd calls; the
  absolute `Bash(~/code/.claude/scripts/gh-bot.sh:*)` entry covers the form used here. A successful
  `DELETE` returns `204` with an empty body — no output is the success case, not a silent failure.
- If the remote branch is protected, the `DELETE` returns `422`; report it and leave the local
  deletion standing rather than retrying.

**Only the original branch needs this — the merged parts delete themselves.** Verified in plan 9d:
`gh api repos/<Repo slug> --jq '.delete_branch_on_merge'` returns **`true`** on app-main, so GitHub
**auto-deletes the head ref of every merged PR**. The rehearsal's merged throwaway had no remote branch
left by the time cleanup ran, while the *closed* one still did. So:

| PR                          | End state         | Remote branch after                         | Explicit `DELETE` needed? |
| --------------------------- | ----------------- | ------------------------------------------- | ------------------------- |
| Each part PR (Skill Step 5) | **merged**        | auto-deleted by `delete_branch_on_merge`    | **No**                    |
| The original PR (§1)        | **closed**        | survives — auto-delete fires on merge only  | **Yes** — §2d's two calls |

Consequences, both load-bearing:

- **Never run the ref `DELETE` for a merged part branch**, and if you do (or if the repo setting is
  off and you are cleaning up after a partial run), treat a **`404`** from that endpoint as
  **already-done success**, not an error: the ref is gone, which is the goal. Only `422` (protected)
  and other non-`204`/non-`404` statuses are reportable failures.
- **§2e's `gmas` reasoning is unaffected.** It is about the *original* branch, which is closed and
  therefore never auto-deleted: it keeps both its upstream and its `origin/<branch>` ref forever, fails
  both of `git-cleanup.sh`'s candidate arms, and is never offered. Do not generalize "merged branches
  vanish on their own" into "cleanup is automatic."
- Do **not** depend on the setting being on. Read it (`gh api repos/<Repo slug> --jq
  '.delete_branch_on_merge'`) if a part's branch still exists after its merge, rather than assuming
  either way.

### 2e. Why `gmas` will never clean this up for you

`/gmas` delegates to `git-cleanup.sh`, whose candidate test (`git-cleanup.sh:75-79`) is exactly two
arms: `upstream:track` equals `[gone]` (the post-merge, post-`fetch -p` case), **or** the branch has no
upstream at all *and* no `refs/remotes/origin/<branch>` exists. A closed-but-not-deleted PR branch
keeps both its upstream **and** its `origin/<branch>` ref forever, so it fails both arms and is
**never offered** — indefinitely. There is no ancestry check anywhere in `gmas`: `--merged`,
`git cherry` and `merge-base` appear nowhere in `git-cleanup.sh` or `gmas/SKILL.md`, and "merged" in
that skill is a synonym for "upstream pruned." (`git-cleanup.sh:73` also skips any branch held by a
worktree, reinforcing §2c.) Deleting the remote ref in §2d is what finally makes `git fetch -p` mark
the local branch `[gone]` — which is why §2d's two calls are both needed and in that order.

---

## 3. Record the split — manifest and `split_into:`, **never the plan's checkboxes**

**Do not touch the plan's `- [ ]` / `- [x]` items, and do not renumber or re-scope its steps.** The plan
is the record of *what was built*; the manifest is the record of *how it shipped*. Editing the
checkboxes to mirror the parts destroys the first record to duplicate the second, and the parts rarely
map one-to-one onto steps anyway (a part can draw from non-adjacent steps — the reason `Plan-Steps:` is
a membership list and not a range).

**Manifest.** `<plan-dir>/split/split-manifest.md` is already complete at this point: Skill Step 5f/5h
wrote `pushed` then `merged` per part. Append a short closing block recording the original PR's closed
state, the `DELETE` outcome (or the user's "leave it" answer from §2b), and the date.

**Plan frontmatter.** Add one key to the plan's existing YAML block, preserving `repo:`, `branch:`,
`worktree:` and the `github_issue*` keys:

```
split_into:
  - { part: 1, branch: <part-1-branch>, pr: <part-1-PR#>, plan_steps: "1, 3" }
  - { part: 2, branch: <part-2-branch>, pr: <part-2-PR#>, plan_steps: "2, 4, 5" }
  - { part: 3, branch: <part-3-branch>, pr: <part-3-PR#>, plan_steps: "none (unrelated changes)" }
```

Safe to add: nothing validates the plan frontmatter key set. `plan-list.sh` reads only
`github_issue:` plus the `## Status` `finished:` value, `git-push` reads only the first ~20 lines for
`github_issue:` / `branch:` / `worktree:`, and `workflow-lint`'s plans group does not police unknown
keys. Keep `split_into:` **below** those keys so it never pushes `github_issue:` or `branch:` out of
`git-push`'s first-20-lines window.

**Leave `branch:` pointing at the original branch** even though §2d just deleted it. It is the
historical fact the plan records, and the manifest is where the live part branches live. See §5c for
what that means the next time someone runs `/git-push` against a part.

---

## 4. Stronghold sync — the final state-mutating point

`split_into:` and the manifest's closing block are stronghold-tracked writes, so they get the same
five-call sequence as Skill Steps 1g and 5i. **One Bash call each:**

1. `cd ~/code` — never a compound `cd … && …` (`block-compound-commands.sh`).
2. `git rev-parse HEAD` — note its literal output.
3. `.claude/scripts/stronghold-sync.sh "<message>"` — **no** `dangerouslyDisableSandbox` (the script is
   allowlisted and sandbox-excluded); keep the message free of `;` `&&` `||` `$(` and backticks.
4. `git rev-parse HEAD` again — compare its literal output against the value noted in (2) **as plain
   text**, never captured into a shell variable. A changed SHA means the sync committed; if instead the
   script printed its own explicit "nothing to commit", confirm that message. **Stop and report if
   neither holds** — an unsynced `split_into:` is a split no later context can reconstruct, and the
   original branch is already gone.
5. `cd ~/code/<repo-dir>` only if more sub-repo work follows; otherwise the run ends in `~/code`.

---

## 5. Downstream skills assume one branch ⇒ one PR — tell the user before they hit it

A split breaks a 1:1 assumption three other skills were written against. None of these is a bug to
silence; each is a prompt or a finding the operator must recognize. **State all three in the hand-off
report**, not after the user is already confused.

### 5a. Hard rule — work the parts **strictly one at a time**

`/pr-review` and `/pr-review-fixer` both key their scratch space off the **plan topic**, not the
branch, with fixed filenames:

| Skill              | Topic-scoped path                                        | Fixed filenames                                                                                                                                    |
| ------------------ | -------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/pr-review`       | `<topic-dir>/tmp/` (`pr-review/SKILL.md:65,193`)         | `pr-diff.patch`, the 8 reviewer files (`plan-conformance.md`, `correctness.md`, …), `coordinator.md`, `coordinator-summary.md` (`SKILL.md:91-104`) |
| `/pr-review-fixer` | `~/code/plans/<repo>/open/<topic>/tmp/` (`SKILL.md:41`)  | `comment-analysis.md` (`SKILL.md:115,160`)                                                                                                         |
| `/git-push`        | `<plan-dir>/tmp/` (`git-push/SKILL.md:118`)              | `correctness.md`, `simplicity.md`, `test-coverage.md`, `consistency.md`, … (`SKILL.md:170-176`)                                                    |

Every part of a split shares one `<topic-dir>`, so two parts worked at once overwrite each other's
files — and `/pr-review` **deletes every file in `<tmp-dir>/`** when it finishes
(`pr-review/SKILL.md:184`), which would wipe a concurrent run's inputs mid-flight. Branch-scoped files
(`push-review-<branch>.md`, `ci-failures-<branch>.md`, `/tmp/claude/coordinator-<branch-slug>.json`) are
safe; the ones above are not. This is the same hard rule `pr-loop.md` §0 states for the push loop, for
the same reason — it holds through review and fixing too, not just through the push.

*(Correction to the tracking plan: `comment-analysis.md` belongs to `/pr-review-fixer`, not
`/pr-review`, and the collision surface is the whole fixed-name set above, not that one file.)*

### 5b. `/pr-review`'s Plan Conformance — what actually happens, and the honest answer

The plan's claim that `/pr-review` "resolves the plan from the branch slug" is **only partly true**, and
the practical consequence is different from what it predicted. `/pr-review` takes a **PR number** as its
argument, never a plan, and resolves the plan in three ordered attempts (`pr-review/SKILL.md:43-51`):

1. **From the PR body** — grep for a reference matching `<topic>/<name>.md`. A part PR body
   (`pr-loop.md` §5) carries scope prose, `Split from #<original>`, `Plan-Steps:` and the issue line —
   **no plan path** — so this attempt normally finds nothing.
2. **From the branch slug** — strip the leading segment and glob `**/*<slug>*.md`. The slug is the
   *part* branch (`…-part-2`), and the glob wraps the slug rather than being wrapped by it, so
   `*soft-delete-part-2*.md` does **not** match `soft-delete.md`. This attempt normally finds nothing
   either.
3. **`AskUserQuestion`** — a picker of candidate plans **plus a "Skip plan conformance" option**.

So in practice a part PR lands on (3). **That option is the correct answer** until the companion change
in §6 lands: Plan Conformance cannot be scoped, so running it against the whole plan produces findings
that are false by construction. If instead the plan *is* resolved — the operator picks it, or the body
happens to cite it — then the plan's prediction holds exactly: the Plan Conformance subagent is
instructed to flag *"does any plan step have no corresponding change in the diff?"* and *"any `- [ ]`
item … that the diff did not address?"* (`pr-review/references/subagent-prompts.md:157,160`), with
`FAIL` on any critical or major finding (`subagent-prompts.md:35-37`). It will emit one missed-step
finding per step the part does not contain and return `FAIL`. **There is no scoping mechanism:** the
strings `Plan-Steps`, `of N`, `step scope` and `subset` appear **nowhere** in `/pr-review` or its
reference — the `Plan-Steps:` header exists only in `/split-pr`, and nothing parses it. Explain the
false-incomplete findings; never argue the reviewer down and never edit the plan to make them go away.

*(`/pr-review-fixer` needs no such warning: it resolves only a **topic directory** from the branch name
(`SKILL.md:18,41`) and never reads a plan file at all, so it has no plan-conformance notion to mis-fire.
That part of the plan's claim is also a correction.)*

### 5c. `/git-push`'s plan-`branch:` assertion fires on every part — the expected prompt and the answer

`/split-pr` itself does not call `/git-push` (Skill Step 5 drives `push-gate.sh`, `<bot-push-script>`
and `pr-finish.sh` directly), so this surfaces when the **user** later runs `/git-push` on a part
branch — pushing review fixes, most often. The plan records `branch: <original-branch>`; the checkout
is `<part-k-branch>`; they differ, so `git-push/SKILL.md:132` stops and asks via `AskUserQuestion`,
offering: **(a)** push the current branch anyway and update the plan's `branch:` to match, **(b)** switch
to the plan's worktree and push `<plan-branch>` from there — *offered only when a verified `worktree:`
frontmatter value exists*, so the prompt often shows just two options — or **(c)** abort.

- **Answer (a).** The push is legitimate; aborting blocks real work, and (b) targets the original
  branch's worktree, which is not where the fix lives — and after §2d that branch no longer exists.
- **(a)'s side effect is acceptable, and the manifest is the authority.** It rewrites the plan's
  `branch:` to the part branch; `<plan-dir>/split/split-manifest.md` remains the record of all N part
  branches, so nothing is lost. Expect the prompt again on the *next* part, now citing the previous
  part's branch — same answer.
- **Never pick anything that checks out `<plan-branch>`.** `git-push/SKILL.md:132` forbids it outright
  and `block-branch-switching.sh` denies it anyway.
- **Gating to know:** the assertion only runs when a plan file was found **and** it has a
  `github_issue:` line **and** a `branch:` line (`git-push/SKILL.md:128-138`). A plan with no linked
  issue never reaches the branch check at all — it surfaces a warning in the final report instead. So
  "the prompt fires on every part" is true for issue-linked plans, which is the normal case.

---

## 6. Required companion change to `/pr-review` — tracked, **not** implemented here

§5b's false-incomplete flag has one real fix, and it belongs to `/pr-review`, not to this skill:

> Teach `/pr-review`'s plan resolution and Plan Conformance subagent to read the **`Plan-Steps:`**
> header from the PR body as a **step-membership list** — a comma-separated list of plan step numbers,
> or the literal `none (unrelated changes)` — and scope the conformance check to exactly those steps,
> skipping the check entirely when the header reads `none`. A membership list, never a contiguous
> `A-B of N` range: a part's files can come from non-adjacent steps.

Tracked as its own plan in the central store:
**`~/code/plans/stronghold/open/pr-review-plan-steps-scoping/pr-review-plan-steps-scoping.md`**.

Until it lands, §5b's guidance stands: choose "Skip plan conformance" when `/pr-review` offers it, and
if conformance does run, explain the findings as expected artifacts of the split. **Do not** work around
it by editing the plan, by splitting the plan file per part, or by omitting `Plan-Steps:` from part
bodies — that header is the input the companion change needs, and writing it today is what makes the
fix a parsing change rather than an archaeology project.

---

## 7. Verified-facts ledger

Checked against the live tree, the hooks, `settings.json` or real command output — not assumed. Eight
entries **correct** the tracking plan's Step 7, or this file, as written. Rows marked **LIVE** were
confirmed by plan 9d's real push → PR → CI → merge → close → delete rehearsal (PRs #815/#816), not by
code-reading.

| Fact                                                                                                                                                                                               | Evidence                                                                                  |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| **CORRECTION (plan 9d)** — this step was never marked **orchestrator-only** although every call in it resolves its target from the Bash cwd. A subagent thread's cwd resets between Bash calls (`cd ~/code/<repo-dir>` then `pwd` → `~/code`), so a delegated Step 6 acts on the stronghold. Same defect 9a/9b found for Skill Step 3. Fixed in §0 and SKILL.md 6 | direct `cd`-then-`pwd` test in a subagent thread; `pr-loop.md` §0                         |
| **Harness also forbids the merge** — `settings.json` `permissions.deny` carries `Bash(.claude/scripts/gh-bot.sh pr merge:*)`, and no `gh pr merge` allow entry exists, so Skill Step 5h's "merging is the human's call" is enforced, not just policy. **But the deny only matches the relative spelling**, while §1 mandates the absolute `~/code/.claude/scripts/gh-bot.sh` — covered by the broad `Bash(~/code/.claude/scripts/gh-bot.sh:*)` allow, which therefore **bypasses that deny**. Never use it to merge; report the gap to the user rather than editing permissions | `settings.json:61` (deny) vs `:194` (allow); no `GH_TOKEN=*gh pr merge*` entry at `:175-186` |
| **CORRECTION (plan 9d) — LIVE** — `delete_branch_on_merge` is **`true`** on app-main, so GitHub auto-deletes the head ref of every **merged** PR: §2d's ref `DELETE` is needed **only for the closed original**, and a `404` from that endpoint on a merged part branch is **already-done success**, not an error. The rehearsal's merged throwaway had no remote branch left; the closed one still did (`git ls-remote` at `35d3d03f` right after the close). §2e's `gmas` reasoning stands — it concerns the *closed* original | `gh api repos/ORG/app-main --jq '.delete_branch_on_merge'` → `true`; `git ls-remote --heads origin` before/after cleanup |
| **LIVE** — a `gh-bot.sh` call from the wrong cwd fails with `GraphQL: Could not resolve to a PullRequest with the number of <N>. (repository.pullRequest)` **even with `--repo`**; the identical call from `~/code/<repo-dir>` succeeds. The error names the PR number but the fault is the cwd (§1) | `gh-bot.sh pr comment 816 --body-file …` run from `~/code`, then from `~/code/app-main`  |
| **CORRECTION — LIVE** — `git branch -D` is **not** prompted: `allow-git-ops.sh` only *defers*, and `Bash(git branch:*)` then allows it. Confirmed live — deleting two throwaway branches in one call produced **no prompt**. The deletion needs the skill's own `AskUserQuestion` gate (§2b) | `allow-git-ops.sh:13-16,83-94`; `settings.json:80`; `branch_guard.py` `BRANCH_MODE_SHORT`; 9d rehearsal |
| **LIVE** — closing a PR **without** `--delete-branch` really does leave the remote branch in place for §2d's separate gated deletion, and the close fires `dev-build-and-deploy.yml` only as a **`skipped`** run (8s) because that workflow gates on `merged == true` — so closing the original costs no staging deploy | `gh pr close 816` then `git ls-remote` → `35d3d03f`; run `37839537043` `skipped` vs merge run `37839402349` `in_progress` |
| **CORRECTION** — `gh pr close` has **no** `--body-file`/`-F`; only `-c, --comment string`. `gh pr comment` has `-F, --body-file`, so the multi-line cross-link goes through a `pr comment` call    | `gh pr close --help`, `gh pr comment --help` (installed `gh`)                             |
| **CORRECTION** — the plan's relative `.claude/scripts/gh-bot.sh` spelling does not resolve from a sub-repo cwd; the absolute `~/code/.claude/scripts/gh-bot.sh` is the runnable, allowlisted one   | `settings.json` (`Bash(~/code/.claude/scripts/gh-bot.sh:*)`); no sub-repo copy exists     |
| **CORRECTION** — `comment-analysis.md` is `/pr-review-fixer`'s file, not `/pr-review`'s, and the collision surface is every fixed-name file in the shared topic `tmp/`, which `/pr-review` deletes | `pr-review-fixer/SKILL.md:115,160`; `pr-review/SKILL.md:65,91-104,184`                    |
| **CORRECTION** — `/pr-review` normally *fails* to resolve the plan for a part PR and falls through to a picker that offers **"Skip plan conformance"**; the flag is conditional, not unconditional | `pr-review/SKILL.md:31,43-51`                                                             |
| **CORRECTION** — `/pr-review-fixer` never resolves or reads a plan file; it infers only a topic directory from the branch name                                                                     | `pr-review-fixer/SKILL.md:18,41`                                                          |
| No step-scoping mechanism exists in `/pr-review`: `Plan-Steps`, `of N`, `step scope`, `subset` appear nowhere in it                                                                                | `pr-review/SKILL.md`, `pr-review/references/subagent-prompts.md`                          |
| Plan Conformance flags missed steps and unaddressed `- [ ]` items, and `FAIL`s on any major/critical finding                                                                                       | `pr-review/references/subagent-prompts.md:35-37,157,160`                                  |
| `/git-push`'s branch assertion is an `AskUserQuestion` with (a) push-and-rewrite, (b) worktree-conditional, (c) abort — gated on the plan having `github_issue:`                                   | `git-push/SKILL.md:128-138`                                                               |
| `gmas` offers only `[gone]`-upstream or upstream-less branches, skips worktree-held branches, and has no ancestry check at all                                                                     | `git-cleanup.sh:36-40,73,75-79`; `gmas/SKILL.md:140`                                      |
| `git push origin --delete` is hook-blocked on an app-push repo; the API ref deletion is the supported path                                                                                         | `block-unsafe-push.sh:229-236`; `settings.json` `api -X DELETE repos/*` entry             |
| A bare `git branch <name>` **is** denied in a sub-repo (verified by running it); `-D` is classified as delete-mode and is not                                                                      | hook output from `block-branch-switching.sh`; `branch_guard.py:45-49`                     |
| git refuses to delete a branch checked out in any worktree, and `-D` does not override that; `make wt-rm` is the sanctioned removal path                                                           | `wt.sh:8,413`; `Makefile:66`                                                              |
| Nothing validates the plan frontmatter key set, so `split_into:` is safe to add                                                                                                                    | `plan-list.sh:22,86`; `git-push/SKILL.md:129-131`                                         |
| `block-bash-writes.sh` blocks `cat >` / `cat <<EOF` / `tee` / `echo >` / `printf >`, naming the brace+quote prompt as the reason                                                                   | `block-bash-writes.sh:21-22`                                                              |
| `gh-bot.sh` is a pure passthrough to `gh` and resolves the App installation from the cwd's `origin`                                                                                                | `gh-bot.sh:23-27,37-45`                                                                   |
