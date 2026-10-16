# Invariant Checks — the Skill Step 3 build and union proof

Skill Step 3 of `/split-pr` builds every part **up front, at the pinned base** and proves the parts
together reproduce the original tree **before** anything is pushed. This file holds the exact command
sequences, the flag-by-flag rationale, the merge-not-`--ff-only` argument, the spurious-failure
catalog, and the mismatch protocol.

Nothing here touches `origin`. Skill Step 3 pushes nothing, opens no PR, and writes **nothing** to the
manifest's `status` column — every part stays `planned` through all of Skill Step 3. Only Skill
Step 4 writes `built-and-verified`, after that part's own local gates pass. A `status` written here
would let a resume (Skill Step 1b) skip gates that were never run.

Conventions carried from Skill Step 1: `BASE` means **paste the literal 40-character SHA** recorded in
the manifest's `base-SHA` column, never `$BASE` and never a shell variable
(`block-command-substitution.sh`, and the Bash tool does not persist shell state between calls).
`<branch>` is the original green branch. Every command runs from inside `~/code/<repo-dir>` unless a
step says otherwise, and each is **its own Bash call** (`block-compound-commands.sh`).

---

## 1. Partition check — run FIRST

It needs only the `.paths` pathspec files Skill Step 2e wrote: **no worktrees, no patches, no
builds.** It is seconds of work, its output is legible, and it short-circuits a doomed split before
any expensive step happens. Never reorder it after the build.

One Bash call each (commands are shown as a list, not a table, because the first one contains a pipe):

1. `git diff --name-only --no-renames BASE...<branch> | LC_ALL=C sort > /tmp/claude/split/all-files.txt`
   — every path the original branch changed, sorted **byte-wise**.
2. `LC_ALL=C sort <plan-dir>/split/*.paths > /tmp/claude/split/union-files.txt`
   — the concatenation of all part pathspecs, sorted the same way.
3. `comm -3 /tmp/claude/split/all-files.txt /tmp/claude/split/union-files.txt`
   — must print **nothing at all**.
4. `uniq -d /tmp/claude/split/union-files.txt`
   — must print **nothing at all**.

> **`LC_ALL=C` on both sorts is mandatory, not hygiene.** `comm` compares byte-wise and *requires*
> its inputs in byte order; a locale sort is not byte order. Reproduced on this machine
> (`LANG=en_US.UTF-8`, `LC_ALL` unset) over PR 749's own 78 paths: a plain `sort` places
> `ENDPOINT_REGISTRY.md` at line 22 while `LC_ALL=C sort` places it at line 1, and it interleaves
> `frontend/home/urls/cards/__tests__/` among its sibling `.ts` files instead of grouping it — because
> the locale's collation ignores `_`, `-` and `.`, which is exactly what source paths are full of.
> Fed those inputs, `comm -3` printed `comm: file 1 is not in sorted order` / `file 2 is not in sorted
> order` **and** `comm: input is not in sorted order` alongside its answer. With `LC_ALL=C` on both
> sorts the same comparison printed the one lost path and nothing else.
>
> The dangerous part is not the warning. **A mis-ordered `comm` can be silently wrong in either
> direction** — skipping a genuinely lost path, or reporting a path as lost that is present — and in
> the all-correct case it prints nothing either way, so the check *looks* like a pass whether or not
> it was meaningful. If you ever see a `not in sorted order` line, the result is void: re-run both
> sorts with `LC_ALL=C` before believing any verdict. `uniq -d` is equally order-dependent, so (2)
> feeds it too.

- **(3) empty proves nothing was lost or invented.** `comm -3` prints the lines unique to either
  side: a line in **column 1** is a changed file assigned to **no** part (lost); a line in **column 2**
  is a path in some part's pathspec that the branch never touched (invented — usually a typo or a
  path copied from the wrong side of a rename). Both inputs must be sorted by the *same*
  `LC_ALL=C sort`, so use the two commands above verbatim rather than mixing in a pre-sorted file or
  dropping the `LC_ALL=C`.
- **(4) empty proves the parts are disjoint.** `uniq -d` prints only paths that appear more than once
  across the pathspec files. The **only** legal duplicates are the paths the slice planner declared
  under its `HUNK-SPLIT SEAMS` heading (`references/slice-planner-prompt.md`). Compare (4)'s output
  against that declared list line by line: an exact match is a pass, anything else is a failure.
  Note what disjointness buys: it is the precondition that makes §5's plain merges trivial.
- Permission notes (verified): `allow-tmp-access.sh` pre-approves **any** Bash command whose text
  matches `/tmp/claude/`, pipes included, so none of these prompt. `sort`, `uniq` and `comm` are all
  in `allow-readonly-bash.sh`'s safe-utility set. Do **not** reach for `cat <file> > <out>` to build
  (2) — `block-bash-writes.sh` blocks `cat` followed by a redirect; `sort` already does the job.
- A failure here is a **planner** defect, not a build defect: go back to Skill Step 2 with the
  specific missing/duplicated path and re-run the planner. Never hand-edit a `.paths` file to make
  this pass — see §7.

---

## 2. One worktree per part — and `git worktree add` is NOT available

**Correction to the plan's assumption.** `git worktree add` is **denied outright** in any sub-repo,
not allowlisted. `.claude/hooks/block-branch-switching.sh` is a registered `PreToolUse(Bash)` hook; it
classifies the command with its `lib/branch_guard.py` helper, whose `judge_worktree()` returns
`{"form": "deny", "hint": "git worktree add"}` for **every** `git worktree add` — no exception for
`-b`, for `--detach`, or for a bare commit-ish. The hook exempts only the stronghold
(`is_stronghold_root` on the resolved toplevel). A hook `deny` **wins over** any `permissions.allow`
entry, so `Bash(git worktree:*)` in `settings.json` does not rescue it.

The same classifier also blocks the hand-rolled alternatives: `git branch <name> <start-point>` is
denied in a sub-repo (only names under `backup/`, with at most two positionals, pass), and
`git checkout -b <name>` is the `create-ahead` form — permitted only when the current branch is the
default branch *and* it is ahead of `origin/<default-branch>`. There is no way to create this step's
branches by hand.

**What works.** The repo's own worktree-creation make target, run from inside the primary clone. The
hook's own header states the rule: scripts run git internally, so only the agent's own Bash command is
judged — and `Bash(make:*)` is allowlisted and sandbox-excluded.

| Need                                 | Use                                                                      | Why                                                                                                                                                         |
| ------------------------------------ | ------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| create a worktree **pinned at BASE** | the repo's own target with its base-ref override                         | only a worktree at `BASE` can prove the union invariant; the union tree equals the original tree **only** at `BASE`                                         |
| list worktrees                       | `git worktree list --porcelain`                                          | not flagged — `judge_worktree()` returns nothing unless the first positional is `add`                                                                       |
| remove a worktree                    | `git worktree remove <path>` (or the repo's `worktree-rm`), **never -f** | `allow-git-ops.sh`'s destructive carve-out matches `worktree:-f` / `worktree:--force` and defers those to a prompt; a clean worktree needs no force         |

**Verified for app-main.** `make worktree-new name=<slug> b=<branch> [from=<ref>]`
(`app-main/Makefile:626`) reaches `scripts/worktree.sh new <name> <branch> <from>`, which sets
`base="${from:-$(default_base …)}"` and runs `git worktree add --no-track -b "$branch" "$path" "$base"`
(`app-main/scripts/worktree.sh:145` and `:97`). `from` is validated as a real commit before anything
is created. So:

```
cd ~/code/app-main                                                     # its own Bash call
make worktree-new name=<part-slug> b=split/<topic>-verify-<part> from=BASE
```

**In a subagent thread, use the `-C` form instead** — cwd does not persist between Bash calls there
(§6.6), so the `cd` above is lost and a bare `make worktree-new` then runs in `~/code`, whose
Makefile has no such target. Unlike the bare-vs-`-C` problem for `git apply`/`git merge`, `make` has
no downside here: `Bash(make:*)` is allowlisted and sandbox-excluded whatever the `-C`, and
`make -C <dir>` sets `CURDIR` to `<dir>`, which is what the repo's own guards read. So:

```
make -C ~/code/app-main worktree-new name=<part-slug> b=split/<topic>-verify-<part> from=BASE
```

Verified for app-main: `worktree-new` **never starts a stack** (the Makefile's own comment says so —
it only creates the worktree, links `.env`/`secrets`, and prints `make up d=1`), so N+1 verification
worktrees cost N+1 *spokes*, not N+1 stacks. Check them against `spokes_ceiling`, not
`suites_ceiling`, in the capacity read below.

**`make wt-new REPO=… BRANCH=…` cannot be used for this.** The stronghold's `wt.sh` invokes
`make -C <repo> worktree-new name=<slug> b=<branch>` with **no** `from=` (`.claude/scripts/wt.sh:229`),
and its generic fallback path bases the branch on `origin/<default-branch>` (`wt.sh:321`). A
verification worktree at `origin/<default-branch>` instead of `BASE` breaks the union invariant **by
construction** — `origin/<default-branch>` has moved past `BASE` by definition whenever anything else
merged in the meantime.

**Degrade, don't improvise.** If the repo's worktree target exposes no base-ref override: **STOP and
report.** Do not substitute `git worktree add` (denied), and do not silently verify at
`origin/<default-branch>`. The one documented fallback is to create the worktree normally and move its
branch back inside it with `git reset --hard BASE` — `git reset` is in neither `settings.json`'s
allowlist nor `allow-git-ops.sh`'s safe set (that hook's comment names `reset` as deliberately
excluded), so it prompts **every time**; acceptable only in an attended run, never under a `/loop`
tick.

**`name=` is not free-form — it must normalize to the branch's own slug.** Verified: app-main's
`worktree-new` refuses `name=pr749-p1 b=split/pr749-verify-p1` with

```
worktree-new: name 'pr749-p1' (slug 'pr749-p1') and branch 'split/pr749-verify-p1' normalize to
different slugs; the directory must match the branch
```

So pass the branch with its `/` turned into `-`: `name=split-pr749-verify-p1
b=split/pr749-verify-p1`. (This also corrects `ci-gates.md` §5's "created with the repo's own
`worktree-new name=<any slug>`" — the slug is not arbitrary.)

**A `BASE` older than the repo's own worktree tooling breaks `worktree-init`, and that is expected.**
`worktree-new` creates the worktree and then runs `make worktree-init` **inside it**, i.e. against
the Makefile **as it existed at `BASE`**. When `BASE` predates that target, the second half fails:

```
make[1]: *** No rule to make target 'worktree-init'.  Stop.
worktree-new: worktree-init failed; the worktree was left in place at …
```

Verified on PR 749: its `BASE` (`cef3677c`, 2026-08-24) carries **zero** `worktree-init` /
`worktree-new` / `worktree-rm` targets, while the primary clone's `main` has all three. What this
means in practice:

- **The worktree itself is created, checked out at `BASE`, and clean** — confirmed with
  `git -C <wt> rev-parse HEAD` and `git -C <wt> status --short`. Exit status 2 here is **not** a
  failure to create.
- **It is fully usable for §1-§5.** The only thing `worktree-init` does is link `.env`/`secrets`, and
  nothing in the partition check, the patch build, the trailer commit or the tree-hash union needs
  either. Proceed.
- **It is NOT usable for Skill Step 4's stack-based gates**, which do need `.env`. On a split whose
  `BASE` is this old, say so and stop rather than faking the gate: a recent branch's `BASE` is recent
  by construction, so this is a stale-`BASE` hazard (a historical PR, a long-lived branch), not a
  universal one.

**At a current `BASE`, `worktree-new` succeeds — and is still not enough for Skill Step 4.** Verified
on plan 9c's fixture (`BASE` = `main`'s tip): `worktree-new … from=<BASE>` created all four worktrees
and `worktree-init` linked `.env`/`secrets` cleanly, exit 0. Nothing in §1-§5 needs more than that, so
Skill Step 3 is complete here. But `worktree-init` installs **no** dependencies and starts **no**
stack, so Skill Step 4's gates need a further one-time init per worktree — `make tools` **and** one
`make up d=1`, in that order, before any gate of any tier. Both failures are documented in
`ci-gates.md` §6d-6 and §6d-7, and the second one is destructive (it caches a wrong-`HOST_UID` image).
Do not read "the worktree is created and clean" as "the worktree is ready to gate".
- **The cleanup forms that go through the repo's Makefile also fail** for such a worktree, for the
  same reason — including `make -C ~/code wt-rm`, which delegates to the repo's `worktree-rm`. Use
  `git -C <primary-clone> worktree remove <wt-path>` (never `--force`), then
  `git -C <primary-clone> branch -D <verify-branch>`. That is safe precisely because
  `worktree-init` never ran, so there is no stack, no volume and no dev DB to leave orphaned — the
  thing `wt-rm` exists to clean up.

**Naming and count.** One worktree per part, plus **exactly one** union worktree for §5 — branches
`split/<topic>-verify-<part>` and `split/<topic>-verify-union`. These are throwaway **verification**
branches that Skill Step 4's cleanup deletes; they are *not* the part PR branches, which Skill Step 5
cuts fresh from the then-current `origin/<default-branch>`. The primary clone is never checked out,
switched, or modified by this step.

**Capacity.** Before creating them, run `make -C ~/code wt-capacity REPO=<repo-dir>` as its own Bash
call, redirect it into `/tmp/claude/wt-capacity-<repo-dir>.txt`, Read that file, and delete it
(`~/code/CLAUDE.md` → Worktree Discipline (4)). Read the N+1 worktrees against **`spokes_ceiling`**:
creating them starts no stacks, so they are spokes. It is **Skill Step 4** that turns each one into a
live stack, and *that* is what `suites_ceiling` / `agents_max` bound — so a split with more parts than
`suites_ceiling` can still be *built* and union-proven here, and must then run Step 4's gates in
batches rather than all at once.

---

## 3. Build each part — the patch recipe

**Correction to the plan's recipe: `git diff` does not support `--pathspec-from-file`.** Verified
empirically on git 2.53.0 — the flag is rejected with `git diff`'s usage text and exit status **129**.
`--pathspec-from-file` exists on `git add`, `commit`, `checkout`, `reset`, `rm`, `stash` and `grep`,
but **not** on `diff`. Pass the paths **literally** after `--`.

One Bash call each:

| #   | Command                                                                                                                     |
| --- | --------------------------------------------------------------------------------------------------------------------------- |
| 1   | `git diff --binary --full-index --no-renames BASE...<branch> -- <path1> <path2> … <pathN> > /tmp/claude/split/<part>.patch` |
| 2   | `test -s /tmp/claude/split/<part>.patch`                                                                                    |
| 3   | `cd <part-worktree>`                                                                                                        |
| 4   | `git apply --index --whitespace=nowarn /tmp/claude/split/<part>.patch`                                                      |

- `<path1> … <pathN>` is the **literal content** of `<plan-dir>/split/<part>.paths`, read with the
  Read tool and spliced into the command — the same "paste the literal, never a variable" rule as
  `BASE`. `$(cat …)` is hard-blocked by `block-command-substitution.sh`; an `xargs`/`cat` pipeline
  either prompts or is blocked. The `.paths` file stays the durable canonical record (the manifest's
  `pathspec-file-path`); this command is only its expansion, so the two can never disagree about
  *which* paths — only about spelling, which §1 already proved.
- **(2) is not optional.** A zero-byte patch means the pathspec matched nothing: a typo'd path, or a
  path that only exists on one side of a rename. Applying an empty patch "succeeds" and produces a
  part with no content, which then shows up as a tree-hash mismatch with no obvious cause.
- **(4) must be bare, not `-C`-prefixed.** `Bash(git apply:*)` matches the bare form;
  `git -C <path> apply …` shifts past that pattern, and `apply` is **not** in `allow-git-ops.sh`'s
  safe set, so the `-C` form prompts. That is exactly why (3) is its own call. The Bash tool's cwd
  does **not** persist across calls in a subagent thread — see §6.6 before relying on it.
- The parts are independent at this stage: §3 can be run for all N parts before §5 starts, and each
  part's worktree holds only its own patch.

### Flag-by-flag rationale

| Flag / token               | Why it is there                                                                                                                                                                                                                                                                                                                                       |
| -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `BASE...<branch>` (3 dots) | the **net** diff against the merge base, so "a later commit modified an earlier commit's file" is already collapsed — that failure mode does not exist, which is what makes path-scoped extraction beat commit-scoped cherry-picking                                                                                                                  |
| `--binary`                 | without it a binary file's hunk degrades to the literal line `Binary files a/… and b/… differ`, which `git apply` cannot apply. The file is then silently absent from the part and the union tree differs with nothing in the patch to explain it                                                                                                     |
| `--full-index`             | emits the full 40-character blob OIDs on each `index` line instead of abbreviations. Abbreviations can be ambiguous in a large repo, and they are precisely what `git apply` reads when it needs to identify a pre-image — including the `--3way` path §3 forbids but §7's diagnosis may inspect                                                      |
| `--no-renames`             | a rename whose two halves land in **different** parts becomes a *one-sided* rename once the diff is path-filtered: the patch declares a `rename from` whose source is outside the pathspec, and cannot apply. Suppressing detection turns it into a plain delete + add, each independently appliable                                                  |
| (same `--no-renames`)      | it must match §1's and the slice planner's input exactly, or the three file lists disagree and §1's proof says nothing about what §3 actually built                                                                                                                                                                                                   |
| `--index` (apply)          | applies to the index **and** the working tree **atomically** — either both or neither. Without it the worktree changes while the index does not, so the following `git commit` commits a *different* tree than the one just built: a wrong part that looks right. It also makes the apply all-or-nothing, so a failure never leaves a half-built part |
| `--whitespace=nowarn`      | git's default is `warn`, which emits a warning per whitespace-suspect **context** line — thousands of noise lines on a large patch. `nowarn` also keeps git from "fixing" whitespace, which would alter content and break tree equality on its own                                                                                                    |

### Never `--3way` on the first pass

On a mismatch, `--3way` reconstructs the pre-image from the blob OIDs and performs a three-way merge.
Two bad outcomes follow. When the merge resolves, it **exits 0** having **synthesized** content that is
in neither the original branch nor the patch — the part looks built, nothing warns, and the union check
later fails with no pointer to where. When it does not resolve, it exits non-zero but has *already*
written conflict markers into the worktree and index, so a careless commit or retry captures them. A
clean plain apply at `BASE` is the entire point of pinning the base: if it fails, **diagnose** (§6),
never let git guess.

### Why `git checkout <head> -- <paths>` was rejected as the build primitive

It is tempting — `Bash(git checkout:*)` is already allowlisted — and it is wrong: **it cannot express
a deletion.** It copies the named paths out of `<head>` into the index and worktree; a file the branch
*deleted* simply stays as it was at `BASE`. That file then belongs to no part, the union tree carries
a file the original tree does not, and the mismatch has no diff to explain it. (It equally cannot
express a mode-only change for a path that no longer exists.) Incidentally it would not even run
prompt-free: `allow-git-ops.sh`'s destructive carve-out matches `checkout:--`, so the `--` form defers
to a confirmation prompt anyway.

---

## 4. Commit each part — provenance trailers, before the union merge

**Ordering is load-bearing.** §5 merges each part's **branch**, so every part must already be a commit
on its branch before §5 runs. A part left applied-but-uncommitted contributes nothing to the merge,
and the union tree then quietly equals "BASE plus the parts that *were* committed" — a mismatch whose
cause is invisible in both tree hashes. Commit immediately after each `git apply`, not after the whole
build.

With the cwd at the part's worktree, one Bash call each:

1. `git status --short` — `--index` already staged everything, so nothing should be unstaged. Anything
   unstaged means the apply did not do what you think: stop (§6.6).
2. `git commit --no-verify -m "<part subject>" --trailer "Original-Commit: <sha> <sha> …" --trailer "Co-Authored-By: <co-author line>" --trailer "Claude-Session: <session URL>"`

- **`--no-verify` is required here, and *only* here.** Reproduced: a plain `git commit` in a fresh
  `BASE`-pinned verification worktree **fails**, because the repo's pre-commit suite runs against a
  tree that has no dependencies installed and no stack up — `worktree-init`/`setup` never ran there
  (and cannot, when `BASE` predates those targets, §2). app-main's actual failure:
  `eslint (ts) … Error [ERR_MODULE_NOT_FOUND]: Cannot find package '@typescript-eslint/parser'` plus
  `TypeScript typecheck … service "vite" is not running`. Without `--no-verify` Skill Step 3 cannot
  reach §5 at all, so the union invariant is unprovable.

- **The *durable* reason is the dependency, not the environment — so do not drop `--no-verify` when the
  environment looks fine.** The failure quoted above came from a `BASE` that predated the repo's
  worktree tooling. At a **current** `BASE`, with `make tools` run in the worktree (`ci-gates.md`
  §6d-6), a plain `git commit` **succeeds**: verified on plan 9c's fixture, where the backend part and
  the unrelated part both committed with all ten pre-commit hooks passing. But a part that **consumes
  an earlier part's artifact is genuinely red at `BASE`**, so its pre-commit can never pass there —
  verified on the same fixture: `typecheck … Failed … exit code 2` /
  `error TS2344: Type '"splitFixtureProbe"' does not satisfy the constraint 'keyof operations'`.
  Since §3 builds every part alone at `BASE`, **at least one part of any dependency-ordered split is
  always red at commit time**, by construction and not by misconfiguration. That is the standing
  justification; the environment story is a second, `BASE`-dependent one. (Skill Step 4 gates the
  *prefix* precisely because of this — `ci-gates.md` §0.)
- **This is not a quality shortcut, and it does not weaken anything.** These commits exist for one
  reason — §5 merges *branches*, so each part needs *a* commit — and Skill Step 4's cleanup deletes
  them with their branches. The real linting and typechecking happen in **Skill Step 4**, through the
  repo's own `make` targets against a properly initialized worktree, and **Skill Step 5 rebuilds and
  re-commits every part on its real PR branch** (created by `make wt-new`, which *does* run the
  repo's setup) with the pre-commit hooks fully active. **Never carry `--no-verify` into Skill
  Step 5's commits** — there it would skip the gate that actually protects the PR.

- **Use `--trailer`, not a multi-line message.** The house heredoc commit form
  `git commit -m "$(cat <<'EOF' … EOF )"` is exempt from `block-command-substitution.sh` (that hook's
  header lists it) but is still **blocked** by `block-bash-writes.sh`, whose first rule matches `cat`
  followed by `<<`. So the heredoc path is unavailable here. `--trailer` (git ≥ 2.32; present in
  2.53.0, verified via `git commit --help`) appends each trailer in git's own trailer block, with **no
  embedded newline anywhere in the command** — the only shape that is both allowlisted
  (`Bash(git commit:*)`) and guard-clean.
- **Repeated `-m` is not a substitute.** Each `-m` becomes its own paragraph, so the trailers end up
  separated by blank lines and are no longer one trailer block.
- `Original-Commit:` lists the squashed SHAs this part covers, read out of
  `<plan-dir>/split/original-log.txt` (Skill Step 1e). Those SHAs may stop resolving in a plain local
  clone once the original branch is deleted; that is expected and not a loss — the archive is the
  record, and for a same-repo PR `git fetch origin 'refs/pull/<N>/*:refs/remotes/pr/<N>/*'` recovers
  every one of them.
- Re-emit the `Co-Authored-By` and `Claude-Session` trailers **verbatim from the session's own
  attribution lines**; do not invent or paraphrase them.
- Commit identity follows the repo's **Bot identity** seam where it has one — the
  `git -c user.name=… -c user.email=… commit …` form is allowlisted as
  `Bash(git -c user.name=* commit *)`.
- Skill Step 5 rebuilds and re-commits each part on its real PR branch with the **same** trailers;
  these verification commits are thrown away with their branches.

---

## 5. The union invariant — authoritative

Create the single union worktree at `BASE` per §2, then, for each part **in merge order**:

| #   | Command                                           | Note                                             |
| --- | ------------------------------------------------- | ------------------------------------------------ |
| 1   | `cd <union-worktree>`                             | its own call; re-issue it if cwd was lost (§6.6) |
| 2   | `git merge --no-edit split/<topic>-verify-<part>` | **bare**, plain merge — see below                |

Then compare two 40-character strings as plain text:

- `git rev-parse HEAD^{tree}` — in the union worktree
- `git rev-parse <branch>^{tree}` — the original green branch

They **must** be equal. (Both forms are allowlisted as `Bash(git rev-parse:*)` and need no quoting;
verified.)

`git merge` must be **bare**: `Bash(git merge:*)` matches only that form, and `merge` is deliberately
absent from `allow-git-ops.sh`'s safe set, so `git -C <path> merge …` prompts.

### Why a plain merge and **not** `--ff-only`

Every part branch is independently rooted at `BASE` (§2) — they are **not stacked** on one another.
The union worktree also starts at `BASE`, so the *first* part can fast-forward. The moment it does,
the union tip **is** part 1's commit; part 2's branch descends from `BASE`, not from part 1, so the
union tip is no longer an ancestor of part 2 and **no fast-forward is possible** for part 2 or any
later part. `--ff-only` therefore fails on part 2 of every split with two or more parts —
structurally, not because anything is wrong. A real merge commit is the correct and expected result.

### Why that is safe rather than sloppy

§1 already proved the parts are **disjoint**: no two parts touch the same path (outside a declared
hunk-split seam). A merge can only conflict on a path **both** sides changed, so every merge in the
sequence is **trivial by construction** — git takes `BASE`'s version from one side and the part's
version from the other, with nothing to resolve and nothing to invent. Consequences:

- A merge that reports a **conflict** is not a merge problem: it **falsifies §1**. Abort, re-run the
  partition check, and treat it as a planner defect (§7). Never resolve such a conflict by hand.
- A declared hunk-split seam is the one path two parts legitimately touch. The merge still resolves it
  as long as the two parts' hunks do not overlap; a conflict there means the seam was mis-declared,
  which is again §1 failing.

### Why tree-hash equality is authoritative anyway

A git **tree object** hashes, recursively, every entry's **path, mode and content OID**. Two commits
with the same tree hash therefore have byte-identical trees — same files, same bytes, same executable
bits, same directory structure. One 40-character comparison catches:

- a changed file assigned to no part (a missing entry),
- a file whose content drifted (a different blob OID),
- a mode-bit change lost or invented (`100644` vs `100755`),
- an extra file that no part should have created.

So the check remains fully authoritative even though a trivial merge cannot conflict: it is testing
the *result*, not the merge.

### Why a plain `git diff` check would lie

`git diff <branch> HEAD` is **rendered** output, filtered by heuristics and configuration the repo may
set:

- **rename detection** (`diff.renames`, on by default) reports a delete+add pair as a single rename —
  so a genuinely misplaced file can read as "a rename, same content";
- **whitespace options** (`-w`, `core.whitespace`, `diff.wsErrorHighlight`, `apply.whitespace`) can
  suppress real differences;
- **`diff.algorithm`** (`histogram`, `patience`) changes hunk attribution, so "no hunks shown" is not
  "no difference";
- **`textconv`** compares a *rendering* of a file rather than the file — two different blobs can
  render identically;
- **`.gitattributes` `-diff`** marks a path binary, reporting only "differ", or nothing useful at all;
- mode-only changes are easy to miss in the default rendering.

`git rev-parse …^{tree}` reads the object store directly and is affected by none of this.

### On mismatch: abort the whole operation

Nothing has been pushed — that is the entire reason the invariant is proven here rather than after
Skill Step 5. So: **stop, report both tree hashes, and never push a partial split.** Do not "fix it in
the next part". Leave the worktrees and patches in place as evidence (Skill Step 4's cleanup is gated
on its gates *passing*). Then use these read-only leads, in order:

1. `git diff --raw --no-renames --abbrev=40 <branch> HEAD` — one line per differing path with **both
   modes and both OIDs** (`:100755 100644 <oid> <oid> M<tab>path`). As literal as the tree hash, and
   it names the path. Untrustworthy as a *verdict*, excellent as a *lead*.
2. `git diff --stat --no-renames <branch> HEAD` — the size of the discrepancy at a glance.
3. §6, in order. The first four entries cover nearly every real case.

---

## 6. Spurious-failure catalog

A mismatch is to be **diagnosed**, not worked around. Each entry: what it looks like, how to confirm
it, what to do.

**6.1 A moving base** — *the primary source of spurious failures.* Any diff, patch or merge taken at a
recomputed merge-base rather than the pinned literal `BASE`. Looks like: a part's patch fails to apply,
or the union tree differs on paths no part was ever assigned. Confirm: re-run
`git merge-base origin/<default-branch> <branch>` and compare its stdout to the manifest's `base-SHA`
**as plain text**. Do: nothing clever — this is Skill Step 1d's resume guard; **stop and report**. A
base that moved means `origin/<default-branch>` diverged from what the in-progress split was built
against, and continuing silently corrupts the invariant.

**6.2 Rename detection** — a rename whose halves fall in different parts. Looks like: `git apply`
reporting `No such file or directory` on a `rename from` path, or the union diff showing a rename.
Confirm: `grep -c '^rename from' /tmp/claude/split/<part>.patch` must be `0`. Do: use `--no-renames`
on **every** diff — partition check, planner input and build alike. If they disagree, §1's proof does
not cover what §3 built.

**6.3 Mode bits** — an executable bit lost or invented. **Verified: `app-main/docker/backup-pipeline-test.sh`
is mode `100755`** (`git ls-files -s`), and it is one of PR 749's unrelated-changes files, so it
crosses a part boundary in the very case this skill was designed against. Looks like: tree hashes
differ with no content difference. Confirm: `git diff --raw --no-renames --abbrev=40 <branch> HEAD` and
read the two mode columns; also check `git config core.fileMode` — when it is `false`, git ignores the
bit in `git status` while the tree hash still differs, which is how this hides. Do: let the patch carry
its `old mode`/`new mode` lines and apply them plainly. Never `--3way` a mode-bearing patch, and never
recreate such a file with `git checkout` or the Write tool — both lose the bit.

**6.4 CRLF / `.gitattributes` normalization** — `core.autocrlf`, `core.eol`, or a `* text=auto` /
`eol=` attribute re-encodes line endings on checkout, so the worktree bytes differ from the blob.
`git apply` then matches context lines against normalized content and either fails or commits
renormalized blobs with new OIDs. Looks like: a patch failing on context lines only, or the union tree
differing on text files no part touched. Confirm: `git config core.autocrlf`, `git config core.eol`,
`git check-attr text eol -- <path>`. **Verified for app-main: there is no `.gitattributes` anywhere in
the tree** (`git ls-files | grep gitattributes` returns nothing), so this hazard is **latent, not
live** for that repo; it stays in the catalog because `/split-pr` is repo-agnostic. Do: never work
around it with `--whitespace=fix` or `--3way` — report it.

**6.5 Submodule gitlinks** — a gitlink is a tree entry of mode `160000` holding a commit OID; `git diff`
and `git apply` handle it through `diff.submodule` rather than as content, so a part that moves a
submodule pointer without its sibling change can leave the union tree pointing at a different commit.
**Correction to the plan's claim.** app-main does track a `.gitmodules`, but that file is the **empty
blob** (`e69de29bb2d1…`) and the index holds **zero** mode-`160000` entries — so app-main declares no
submodules at all and has no gitlinks. The plan's "app-main has a `.gitmodules`" is literally true but
reads as if the hazard were live; it is not. Confirm, for any repo: `git ls-files -s | grep '^160000'`
and `git diff --raw --no-renames BASE...<branch> | grep 160000`. Do: if gitlinks do appear, keep every
gitlink and its `.gitmodules` edit in the **same** part (they are an atom) and verify with
`git diff --raw`, never a rendered diff.

**6.6 A `git apply` or `git merge` that ran in the wrong tree** — the `cd`-then-bare-command pattern
depends on the Bash tool's cwd persisting between calls, and **it does not persist in a subagent
thread**. Looks like: the apply "succeeds" but the part's worktree is empty, or the primary clone is
suddenly dirty. Confirm: run `git rev-parse --show-toplevel` and `git status --short` as their own call
immediately before each bare `git apply` / `git merge`. Do: if cwd does not persist, use
`git -C <worktree> apply …` / `git -C <worktree> merge …` and accept the one approval prompt each —
never guess, and never run the bare form hoping the cwd held.

**This is why Skill Step 3 is orchestrator-only, exactly like Skill Step 5.** An approval prompt that
nobody is present to answer is not a fallback, so the whole of §3-§5 must run in the orchestrator's
own thread, where the `cd`-then-bare sequence works. Do **not** delegate the build or the union merge
to a subagent — delegate the *analysis* (Skill Step 2's planner) and nothing else. If you nonetheless
find yourself executing §3 in a thread where cwd does not persist, the `-C` forms can be made
prompt-free by keeping `/tmp/claude/` in the command text — `allow-tmp-access.sh` pre-approves any
Bash command whose **text** matches that prefix, which the patch path already does for `git apply`,
and which a `> /tmp/claude/split/merge-<part>.txt 2>&1` redirect supplies for `git merge` (desirable
anyway, per the redirect-test-output house rule). Treat that as a documented escape hatch for a
read-only rehearsal, not as a licence to delegate a real split.

---

## 7. Mismatch protocol

1. **Stop.** Skill Step 3 pushes nothing, so nothing is public. Report, do not retry.
2. **Keep the evidence** — the worktrees, the `/tmp/claude/split/*.patch` files and both tree hashes.
3. **Walk §6 in order.** 6.1–6.4 cover nearly every real case.
4. **A planner defect** (a file in no part, a file in two, an atom cut, a mis-declared seam) goes back
   to **Skill Step 2** with the specific defect, and the planner re-runs. **Never hand-edit a `.paths`
   file to make a check pass**: the manifest is what the user accepted at 2d, and changing the split
   silently is exactly what Skill Step 4's stop-and-report rule forbids.
5. **An environmental cause** (6.1, 6.4, 6.6) is fixed in the environment, and then the **whole** of
   Skill Step 3 re-runs from the partition check. Never resume mid-way — a partially rebuilt set of
   parts is unprovable.
6. **Never** reach for `--3way`, `--whitespace=fix`, `--reject`, or a manual edit of a part's worktree
   to force the trees to match. Each of those makes the union invariant unfalsifiable, which is worse
   than a failing check.

---

## 8. Verified-facts ledger

Everything above that contradicts, or sharpens, the tracking plan — checked against the real tree, the
real hooks and real command output rather than assumed.

| Claim under test                                               | Verdict                                                                     | Evidence                                                                                       |                             |
| -------------------------------------------------------------- | --------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- | --------------------------- |
| `git worktree` is "allowlisted" for per-part worktrees         | **WRONG** — `git worktree add` is hook-**denied** in every sub-repo         | `block-branch-switching.sh` + `branch_guard.py` `judge_worktree()`; a hook deny beats an allow |                             |
| `git diff --pathspec-from-file=<file>` builds the part patch   | **WRONG** — `git diff` has no such flag                                     | run on git 2.53.0: usage text, exit **129**. Use literal paths after `--`                      |                             |
| `git diff --binary --full-index --no-renames … -- <paths>`     | works, and `git apply --index --whitespace=nowarn` accepts its output       | generated and `git apply --check -R --index --whitespace=nowarn`-verified                      |                             |
| `git checkout <head> -- <paths>` cannot express deletions      | correct — and it also trips `allow-git-ops.sh`'s `checkout:--` carve-out    | `allow-git-ops.sh` destructive-variant table                                                   |                             |
| a heredoc commit message trips a guard                         | correct, but the guard is `block-bash-writes.sh` (`cat` + `<<`)             | that hook's first rule; `block-command-substitution.sh` actually *exempts* the heredoc form    |                             |
| `git commit --trailer` is available                            | yes                                                                         | `git commit --help` on git 2.53.0                                                              |                             |
| `docker/backup-pipeline-test.sh` is `100755`                   | **correct**                                                                 | `git -C app-main ls-files -s docker/backup-pipeline-test.sh`                                   |                             |
| "app-main has a `.gitmodules`" ⇒ gitlink hazard is live        | misleading — the file is the **empty blob**, 0 gitlinks in the index        | `ls-files -s .gitmodules` = `e69de29…`, and no `^160000` entry in `ls-files -s`                |                             |
| CRLF/`.gitattributes` normalization is a live app-main risk    | no — app-main tracks **no** `.gitattributes` at all; keep it generic        | `git -C app-main ls-files` lists no `.gitattributes` anywhere                                  |                             |
| `make wt-new` can create a BASE-pinned worktree                | **no** — it passes no `from=`, and its generic path uses `origin/<default>` | `.claude/scripts/wt.sh:229`, `:321`                                                            |                             |
| app-main's own `worktree-new` can pin a base                   | **yes** — `from=<ref>` → `git worktree add --no-track -b <b> <path> <from>` | `app-main/Makefile:626`; `app-main/scripts/worktree.sh:145`, `:97`                             |                             |
| `allow-tmp-access.sh` pre-approves the `/tmp/claude/` commands | yes — any command whose **text** matches `/tmp/claude/`, pipes included     | that hook's single regex                                                                       |                             |
| `sort` / `uniq` / `comm` need no permission entry              | yes — all three are in `allow-readonly-bash.sh`'s safe-utility set          | `SAFE_CMDS` in that hook                                                                       |                             |
