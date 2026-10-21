# PR Loop — the Skill Step 5 sequential push → PR → CI → merge cycle

Skill Step 5 of `/split-pr` ships the parts **one at a time**: cut part *k*'s branch from the
then-current `origin/<default-branch>`, rebuild its patch, gate it, push it, open its PR, wait for CI,
hand back to the human for the merge, **confirm** that merge programmatically, then start part *k+1*.
This file owns every command, every hook interaction, the manifest writes a resume depends on, and a
verified-facts ledger. SKILL.md's Skill Step 5 is the contract; this is the procedure.

Nothing here is optional and nothing here may be reordered: each step exists because a specific hook,
script or GitHub behavior was verified to refuse the alternative.

---

## 0. Conventions and the two hard rules

- **Skill Step 5 is orchestrator-only — never delegate it to a subagent.** Three of its commands
  (`push-gate.sh`, `<bot-push-script>`, `pr-finish.sh`) resolve their target from the **Bash cwd**, and
  a subagent thread's cwd does **not** persist between Bash calls. This mirrors `git-push/SKILL.md`'s
  own orchestrator-only cwd rule for exactly the same three scripts. If the cwd cannot be set to the
  part's checkout, **STOP and report** — do not substitute a `-C` form.
- **Every `cd` is its own Bash call**, never compounded (`block-compound-commands.sh`).
- **Hard rule 1 — one part at a time, strictly.** Part *k+1*'s branch is not cut until part *k*'s merge
  is **confirmed** (§8). `/pr-review` and `/pr-review-fixer` also share one `<topic-dir>/tmp/` and one
  `comment-analysis.md` per plan, so two parts worked concurrently collide there too.
- **Hard rule 2 — never advance on an assumption.** A human saying "merged" in chat is not evidence.
  Only the §8 check writes `merged`.

---

## 1. The loop, per part *k*

| #   | Action                                                           | Where it runs              | Manifest write           |
| --- | ---------------------------------------------------------------- | -------------------------- | ------------------------ |
| 1   | `git fetch origin` in the primary clone                          | `~/code/<repo-dir>`        | —                        |
| 2   | Cut `<part-branch>` from `origin/<default-branch>` (§2)          | `~/code` (`make wt-new`)   | `branch`                 |
| 3   | Rebuild part *k*'s patch at the literal recorded `base-SHA` (§3) | `~/code/<repo-dir>`        | —                        |
| 4   | Apply it, commit it with its provenance trailers (§3)            | `<part-wt>`                | —                        |
| 5   | Delete `<scratch>/<part>.patch` (§3)                     | anywhere                   | —                        |
| 6   | Run the push gate, then push via `<bot-push-script>` (§4)        | `<part-wt>`                | —                        |
| 7   | Open the PR, then `pr-finish.sh` (§5)                            | `<part-wt>`                | `PR#`, `status: pushed`  |
| 8   | Wait for CI; fetch failure logs if red (§7)                      | `<part-wt>` / `~/code`     | `CI-status`              |
| 9   | **Hand back to the user for the merge** (§8)                     | —                          | —                        |
| 10  | Confirm the merge; only then record it (§8)                      | `<part-wt>`                | `merge-status`, `status` |
| 11  | Sync the manifest to the stronghold (§9)                         | `~/code`                   | —                        |

On a resume, Skill Step 1b's `status` column says where to re-enter: `built-and-verified` starts at
row 1, `pushed` skips rows 1-7 and starts at row 8.

---

## 2. Cutting part *k*'s branch — `make wt-new`, **not** `git checkout -b`

**`git checkout -b <part-branch>` is hook-DENIED here.** `block-branch-switching.sh`'s classifier
(its `lib/branch_guard.py` helper, form `create-ahead`) allows `git checkout -b` in a
sub-repo **only** when the current branch *is* the default branch **and** that branch is ahead of
`origin/<default-branch>`. A part branch is cut from a freshly-fetched default branch that is by
definition *not* ahead, so the create is denied — and a hook deny beats any `permissions.allow` entry.
`git branch <name>` is denied too (only `backup/…` names are allowed), and `git worktree add` is denied
outright.

The one available creation path is the stronghold's own worktree target, run from `~/code` as its own
Bash call:

```
git fetch origin                                   # in ~/code/<repo-dir>, its own call
make wt-new REPO=<repo-dir> BRANCH=<part-branch>    # from ~/code, its own call
```

`wt.sh` creates the branch with `git worktree add --no-track -b <part-branch> <path>
origin/<default-branch>` — **basing on `origin/<default-branch>` is exactly the semantics Skill Step 5
needs** (unlike Skill Step 3, where that same behavior is wrong and the repo's own `from=<BASE>`
override is mandatory). `wt.sh` does **not** fetch for you: it only verifies the ref exists and dies
with `run git fetch there first` otherwise. Hence the explicit fetch in step 1 — and fetching *per
part* is what makes part *k+1* sit on top of part *k*'s squash-merge.

- **Capacity first.** The push gate runs suites, so before step 6 run
  `make -C ~/code wt-capacity REPO=<repo-dir>` as its own Bash call, redirect to
  `/tmp/claude/wt-capacity-<repo-dir>.txt`, Read it, delete it. `suites_ceiling: 0` ⇒ AskUserQuestion
  before starting; `n/a` ⇒ proceed.
- **Repo with no worktree support** (`make wt-new` reports `has no worktree support`): the agent
  **never** creates the branch itself — the hook denies every form. STOP and print the manual commands
  for the user to run in their own terminal (`git checkout <default-branch>`, `git pull --ff-only`,
  `git checkout -b <part-branch>`), then resume. This is the same no-worktree path
  `git-push/SKILL.md` Step 0 already documents.

### Why fresh per part, rather than pre-cutting every branch

`push-gate.sh --precheck` (and the full gate) computes staleness with `git cherry
origin/<default-branch> HEAD` and exits **3 / `STALE`** when any commit is marked `-`. A squash-merge
rewrites part *k*'s content into a single new commit on the default branch, so **every** branch that
was cut before that merge and carries the same change now has commits `git cherry` marks as already
present. Pre-cutting all N branches up front therefore makes parts 2..N stale the moment part 1
merges, and the gate — and with it `<bot-push-script>` — hard-refuses them. Cutting part *k+1* only
*after* part *k*'s merge is confirmed keeps `git cherry` clean by construction. The same argument is
why part *k+1* must never be chained off part *k*'s branch (§8).

---

## 3. Rebuilding the patch, applying it, committing it

**This is a fresh rebuild, not a reuse.** Skill Step 4's cleanup already deleted every
`<scratch>/<part>.patch` once the local gates passed; only the durable
`<plan-dir>/split/<part>.paths` files survived. Rebuild from them:

1. **Read part *k*'s `base-SHA` from `split-manifest.md`** — the literal 40-character SHA recorded
   once in Skill Step 1d. **Never recompute it here**, and never put it in a shell variable: Bash state
   does not persist between calls and `$(...)` is blocked by `block-command-substitution.sh`. Paste the
   literal SHA into the command.
2. **Build the patch** from `~/code/<repo-dir>` (one Bash call):

   ```
   git diff --binary --full-index --no-renames <literal-base-SHA>...<branch> -- <path1> … <pathN> > <scratch>/<part>.patch
   ```

   **`git diff` has no `--pathspec-from-file`** — verified on git 2.53.0, it exits **129** with git
   diff's own usage text (the flag exists on `add`, `commit`, `checkout`, `reset`, `rm`, `stash` and
   `grep`, not `diff`). So the `.paths` lines are **spliced in literally** after `--`. Flag rationale
   and the full catalog of what each one prevents: `invariant-checks.md` §3.
3. **Apply it inside `<part-wt>`** — `cd <part-wt>` as its own call, then, as another call, **bare**:

   ```
   git apply --index --whitespace=nowarn <scratch>/<part>.patch
   ```

   Bare, not `-C`-prefixed: `Bash(git apply:*)` matches only the bare form, and `apply` is **not** in
   `allow-git-ops.sh`'s safe set, so a `git -C <wt> apply …` prompts every time. **Never `--3way`** —
   it can synthesize content and silently break the union invariant that Skill Step 3 proved.
4. **Commit with the provenance trailers** (one call, from `<part-wt>`):

   ```
   git commit -m "<single-line subject>" --trailer "Original-Commit: <sha1>, <sha2>, …" --trailer "Co-Authored-By: …" --trailer "Claude-Session: …"
   ```

   - A **heredoc message is blocked** by `block-bash-writes.sh` (its regex matches `cat` followed by
     `<<`), not by the substitution guard — which actually exempts the heredoc form. One single-line
     `-m` plus repeated **`--trailer`** flags is the working shape (`git commit --trailer` confirmed
     present in git 2.53.0).
   - **Repeated `-m` would break the trailer block**: git joins successive `-m` values as separate
     paragraphs, so the trailers would no longer be one contiguous final block and would stop being
     parsed as trailers.
   - **No `--no-verify` here.** Skill Step 3's *verification* commits need it (their `BASE`-pinned
     worktrees have no deps installed and no stack, so the pre-commit suite cannot run —
     `invariant-checks.md` §4). This commit is the opposite case: `<part-wt>` came from
     `make wt-new`, which ran the repo's own setup, so the hooks work and they are the gate that
     protects the PR. If the pre-commit suite fails **here**, that is a real finding about the part —
     fix it or stop, never skip it.
   - `Original-Commit:` lists the squashed original SHAs this part covers — read them from
     `<plan-dir>/split/original-log.txt` (Skill Step 1e). `Co-Authored-By:` and `Claude-Session:` are
     re-emitted verbatim from the same archive, because the squashed originals are the only record of
     them once the original branch is deleted.
5. **Delete `<scratch>/<part>.patch`** as soon as the commit exists — the same
   ephemeral-patch hygiene Skill Step 4 applies to the verification patches. The `.paths` file stays.

---

## 4. The push gate, then the push

**The push gate is not optional and is not `/git-push`'s business — it is a hard precondition of the
push itself.** `<bot-push-script>` (`gh-app-push.sh`) runs
`python3 lib/push_gate.py check <branch>` and **exits 6 — "no passing push-gate result for this branch
at HEAD"** when none exists, and the registered `require-push-gate.sh` PreToolUse hook refuses the
command for the same reason. So, from `<part-wt>` (each its own Bash call):

```
~/code/.claude/scripts/push-gate.sh <part-branch>
<bot-push-script> <part-branch>
```

- The gate maps the part's changed paths onto the repo's **Push gate** seam rows, runs those suites,
  and writes `<git-common-dir>/push-gate/<sha>.json`. Exit 0 = pass or `na`; **1** a suite failed;
  **3** a precondition (stale, dirty tracked files, HEAD not `refs/heads/<part-branch>`, nothing to
  push); **4** could not run. Anything non-zero: **STOP and report** — never push past it.
- Any commit made **after** the gate ran invalidates its result (it is keyed to the HEAD sha), so
  re-run the gate after any amend.
- **The push command runs alone.** `block-compound-commands.sh`'s `git push` rule is `ops: **all**`,
  so a chain operator *or a pipe* denies it. The bot-script spelling does not contain the token
  `git push`, but keep it alone anyway: nothing chained, nothing piped, no `| tee`.
- **No `dangerouslyDisableSandbox` on the bot push script.** It is declared in
  `sandbox.excludedCommands` (both the relative and the absolute spelling), so it already runs
  unsandboxed — which is what lets it persist upstream tracking in `.git/config`. Adding the flag only
  forces an un-suppressible prompt.
- **SSH fallback only.** A repo whose seam says `Bot identity: n/a` has no bot script: then a plain
  `git push origin <part-branch>` runs as its own Bash call **with**
  `dangerouslyDisableSandbox: true` (SSH needs it), and upstream tracking is set afterwards with a
  separate `git branch --set-upstream-to=origin/<part-branch> <part-branch>` (also unsandboxed) — see
  `~/code/CLAUDE.md` → "`git push -u` and sandboxed `.git/config`".
- The script refuses the default branch and refuses every force flag; pass the **literal** branch
  name, never `HEAD`, a refspec or a variable.

---

## 5. Opening the PR

**Compose the body as a file, not a heredoc.** `block-bash-writes.sh` blocks the
`--body "$(cat <<'EOF' … EOF)"` shape that `git-push/SKILL.md` prints, so write the body with the
**Write tool** to `<scratch>/<part>-pr-body.md` and pass `--body-file`. From `<part-wt>`:

```
GH_TOKEN=$(<token-generator>) gh pr create --repo <Repo slug> --base <default-branch> --head <part-branch> --title "[<area>] <part scope> (split <k>/<N>)" --body-file <scratch>/<part>-pr-body.md
```

`Bash(GH_TOKEN=*gh pr create*)` is allowlisted, and `block-unsafe-push.sh` **requires** the
`GH_TOKEN=$(...)` prefix on every `gh` call in an app-token repo — a bare `gh pr create` is blocked
outright, not merely unapproved. Honor the repo's PR template if it has one, exactly as
`git-push/SKILL.md` Step 9 describes; the lines below then go inside/below its sections.

**Every part's body carries all four of these:**

| Line                                   | Content                                                                                         |
| -------------------------------------- | ----------------------------------------------------------------------------------------------- |
| scope                                  | what *this* part contains, in prose — the reviewer must not have to diff to learn the boundary  |
| `Split from #<original-PR#>`           | plus a full link to the original PR, and to the sibling parts already merged                    |
| `Plan-Steps: 2, 4, 5`                  | the part's step-membership list, **verbatim** from the manifest's `plan-steps` column           |
| `Closes #<issue>` / `Part of #<issue>` | issue wiring per the table below                                                                |

- **`Plan-Steps:` is a membership list, never a range.** Copy the manifest cell as-is: a
  comma-separated list of plan step numbers, because a part's files may come from non-adjacent steps.
  Never render it as a contiguous `A-B of N`. The unrelated-changes part maps to no plan steps at all,
  so its header is the literal `Plan-Steps: none (unrelated changes)` — never an empty value and never
  an omitted header. **This is the header the companion `/pr-review` change (plan Step 7) will parse**
  to scope its Plan Conformance check to exactly those steps; until that change lands, `/pr-review`
  flags every part as incomplete and that must be explained to the user, not silenced.
- **Issue wiring — the `Closes` trap.** `/git-push` appends `Closes #<issue>` automatically from the
  plan's `github_issue:` frontmatter. Reused naively for every part, all N parts close the same issue
  and the **first** merge closes it prematurely, while the remaining parts are still unmerged.

| Part in merge order              | Issue line                                      |
| -------------------------------- | ----------------------------------------------- |
| every part except the last       | `Part of #<issue>`                              |
| the **last** part                | `Closes #<issue>`                               |
| the unrelated-changes part       | `Closes #<its own issue>` — see below           |

  The unrelated-changes part is **not part of the original work**, so it gets its **own** issue:
  `GH_TOKEN=$(<token-generator>) gh issue create --repo <Repo slug> …` (allowlisted), with a single
  inferred label from the repo's real label set, and `Closes` that one. It never carries `Part of` or
  `Closes` for the original issue. `Part of #N` is the master-plan convention and creates the
  cross-reference without any auto-close behavior.

**Then run the plumbing script, not inline GraphQL** (from `<part-wt>`):

```
~/code/.claude/scripts/pr-finish.sh <part-PR#> --labels "label1,label2" --milestone "<milestone title>"
```

`pr-finish.sh` already **reuses `git-push`'s `references/graphql/*.graphql` files** — it passes
`--graphql-dir=$STRONGHOLD/.claude/skills/git-push/references/graphql` to `lib/pr_finish.py`, which
reads `get-pr-node-id`, `find-project`, `find-project-user`, `get-status-field`,
`get-pr-project-items`, `add-to-project`, `set-status`, `find-bot` and `assign-bot` from there (all
nine verified present). Do **not** re-author any of that GraphQL inline. The script also validates
label and milestone names before changing anything (exit 3 lists the valid ones), adds the PR to the
seam's project board, assigns the seam's Bot identity, and **requests the seam's `PR reviewer`**.

**The reviewer is mandatory on every part.** `~/code/CLAUDE.md` requires `OWNER` on every PR in
every repo, **including OWNER-owned personal ones** (the PR author is the bot, so GitHub accepts
it). If `pr-finish.sh` logs a skipped reviewer, add it explicitly:
`~/code/.claude/scripts/gh-bot.sh pr edit <part-PR#> --add-reviewer OWNER` — run it **from inside
the sub-repo checkout**, because `gh-bot.sh`'s token generator resolves the App installation from the
cwd's `origin` remote (via `GH_APP_REPO` / `git remote get-url origin`), not from `--repo`.

**Write `pushed` into the manifest's `status` column now** (plus `branch` and `PR#`), the moment the
branch is pushed and the PR is open. This is the write Skill Step 1b's resume path reads to skip §2-§5
entirely and re-enter at §7-§8 — the expensive, irreversible work it must never repeat.

---

## 6. Manifest discipline

The manifest is the only state that survives a fresh context, and each `/loop` tick is context-free.
Update it **after every part**, never in a batch at the end.

| Column         | Written when                                                      |
| -------------- | ----------------------------------------------------------------- |
| `branch`       | §2, as soon as the branch exists                                  |
| `PR#`          | §5, as soon as the PR is open                                     |
| `status`       | `pushed` at §5; `merged` at §8, only after the check confirms it  |
| `CI-status`    | §7, from the `gh pr checks` result                                |
| `merge-status` | §8, `merged` — written in the **same** step as `status: merged`   |

Write it with the **Write tool** (`block-bash-writes.sh` blocks `cat >`, `tee`, `echo >`, `printf >`).

---

## 7. Waiting for CI, and reading a failure

```
GH_TOKEN=$(<token-generator>) gh pr checks <part-PR#>
```

Allowlisted as `Bash(GH_TOKEN=*gh pr checks*)`; the prefix is mandatory for the §5 reason. Exit **8**
means checks are still pending, **1** means at least one failed, **0** means all concluded
successfully; `--json bucket,name,state` buckets each check as `pass`/`fail`/`pending`/`skipping`/
`cancel` if a machine-readable read is easier.

- **Poll with the Monitor tool or `/loop` dynamic mode, re-running that same command each tick.**
  **Never a shell poll loop** — `block-polling-loops.sh` denies `until`/`while`/`for … sleep N … done`
  and a foreground `sleep` is blocked outright. `gh pr checks --watch` is also a poor fit: it holds one
  Bash call open for the whole CI run.
- **Fetch failure logs through the wrapper**, which exists specifically to survive the sandbox's
  `~/.cache/gh` write denial (`operation not permitted: ~/.cache/gh/run-log-*.zip`) — a bare
  `gh run view --log-failed` cannot work, and `dangerouslyDisableSandbox` is itself rejected on bare
  `gh` calls:

  ```
  .claude/scripts/gh-log-fetch.sh --summary run view <run-id> --repo <Repo slug> --log-failed
  ```

  **Run it from `~/code`** (its own `cd` call), which is what the relative — and allowlisted
  (`Bash(.claude/scripts/gh-log-fetch.sh:*)`) — spelling resolves against, and what the script itself is
  written for: `--repo <Repo slug>` is **mandatory**, because the wrapper exports it as `GH_APP_REPO` so
  the token targets *that* repo's App installation instead of the stronghold's cwd-derived one. It needs
  neither a `GH_TOKEN=$(...)` prefix nor `dangerouslyDisableSandbox` — it mints its own token and
  redirects gh's cache into `/tmp/claude/gh-cache`. This is the exact shape `pr-review-fixer/SKILL.md`
  already uses. **Only** when a repo's **Token generator** seam differs from the wrapper's own
  resolution order (`$GH_TOKEN_GENERATOR`, then `<git-root>/.claude/bot/generate-gh-token.sh`, then the
  tracked stronghold generator) prefix it `GH_TOKEN_GENERATOR=<token-generator>`, which the
  `Bash(GH_TOKEN_GENERATOR=*gh-log-fetch.sh*)` entry allowlists.
- **A red part is not a reason to improvise.** Investigate per `~/code/CLAUDE.md` → "Test Failures:
  Investigate, Don't Dismiss". A failure that is genuinely a slicing defect goes **back to the user**
  and back through Skill Step 2's acceptance — never silently re-assign files between parts.

---

## 8. Hand back for the merge, then **confirm** it

**Merging is the human's call — and the harness enforces it, not just this skill.** `settings.json`'s
`permissions.deny` carries `Bash(.claude/scripts/gh-bot.sh pr merge:*)` and there is no
`Bash(GH_TOKEN=*gh pr merge*)` allow entry, so an agent has no sanctioned way to merge a part PR.
**Do not route around that** with the absolute `~/code/.claude/scripts/gh-bot.sh pr merge` spelling:
the deny matches only the relative spelling, so the broad `Bash(~/code/.claude/scripts/gh-bot.sh:*)`
allow lets it through — a gap in the guardrail, not permission to use it. Report the gap; never close
it by editing permissions yourself.

Stop at a green PR, report it, and hand back. The skill resumes from `split-manifest.md` when
re-invoked — which is why §6's writes happen before the hand-back and not after.

When re-invoked, **confirm the merge before cutting part *k+1***, from `<part-wt>` or the primary
clone:

```
GH_TOKEN=$(<token-generator>) gh pr view <part-k-PR#> --json state,mergedAt
```

`state: MERGED` with a non-null `mergedAt` is the confirmation — verified live in plan 9d
(`state: MERGED`, `mergedAt: 2026-10-08T20:25:57Z`, plus the merge commit sha from the same read, which
is worth recording in the manifest row). Fallback:
`git cherry origin/<default-branch> <part-k-branch>` after a fetch — every commit marked `-` is
already in the default branch. Only once one of those confirms it, write `merged` into **both**
`merge-status` **and** `status` for part *k*. **A human's "merged" message in chat is not evidence**:
a row can go stale when the process was interrupted right after the merge but before the skill recorded
it, and advancing on the assumption cuts part *k+1* from a default branch that does not yet contain
part *k* — producing a PR that re-introduces part *k*'s diff.

**Two things the merge does on its own — do not undo or re-do either.**

- **The remote part branch deletes itself** when the repo sets `delete_branch_on_merge` (verified
  `true` on app-main: `gh api repos/<Repo slug> --jq '.delete_branch_on_merge'`). So **never** run the
  `gh-bot.sh api -X DELETE …/git/refs/heads/<part-branch>` form on a merged part — that call belongs to
  Skill Step 6 and the **closed original** branch only (`disposition.md` §2d). If you do run it, a
  **`404`** means the ref is already gone: treat it as success, not an error. The *local* part branch
  does survive (and `make wt-rm` prints `(branch kept)`), so it is the local side that §2c/§2d's
  cleanup and `gmas` deal with.
- **One staging deploy fires per merged part.** `dev-build-and-deploy.yml` gates on
  `pull_request: closed` **and** `merged == true`, verified both ways in plan 9d: the merge started a
  real run (`in_progress`), while a plain **close** started a run that `skipped` in 8s. So N parts ⇒ N
  staging deploys and a closed PR costs nothing — which is why SKILL.md 2d's confirmation prompt says
  "one staging deploy per **merged** part" and why Skill Step 6's close of the original is deploy-free.

Then **re-fetch** and cut part *k+1* from the updated `origin/<default-branch>` (§2, step 1).

**Never chain the remainder off part *k*'s branch.** It is tempting (part *k+1* "needs" part *k*), but
the squash-merge turns part *k*'s commits into stale duplicates of a single new default-branch commit:
`push-gate.sh`'s `git cherry` staleness check then marks part *k+1* **STALE** and exits 3, the
`require-push-gate.sh` hook refuses the push for want of a passing result, and the PR — if it somehow
opened — would show part *k*'s diff a second time. Worse, a PR based on `part-k` rather than the
default branch runs **zero** checks in a `pull_request: branches: [<default>]` workflow, so it is not
merely awkward but unverifiable. Sequential-off-the-default-branch is the only topology that works.

---

## 9. Per-part stronghold sync

Once part *k*'s row (`branch`, `base-SHA`, `PR#`, `CI-status`, `merge-status`, `status`) is updated —
i.e. after the merge is confirmed in §8 — sync it, **one Bash call each**:

1. `cd ~/code` — never a compound `cd … && …`.
2. `git rev-parse HEAD` — note its literal output.
3. `.claude/scripts/stronghold-sync.sh "<message>"` — **no** `dangerouslyDisableSandbox` (the script is
   allowlisted and sandbox-excluded); keep the message free of `;` `&&` `||` `$(` and backticks.
4. `git rev-parse HEAD` again — compare its literal output against the value noted in (2) **as plain
   text**, never captured into a shell variable. A changed SHA means the sync committed; if instead the
   script's own output explicitly said there was **nothing to commit**, confirm that message instead.
   **Stop and report if neither holds** — an unsynced manifest is a split that a fresh context cannot
   resume.
5. `cd ~/code/<repo-dir>` — before cutting part *k+1*'s branch.

---

## 10. Verified-facts ledger

Everything below was checked against the live tree, the hooks, the settings file or real command
output — not assumed. Five entries **correct** the tracking plan's Step 6 as written. Rows marked
**LIVE** come from plan 9d's real rehearsal (PRs #815/#816: push gate → bot push → `pr-finish.sh` →
CI → human merge → close → branch cleanup), not from code-reading.

| Fact                                                                                                                                                                                                                                                                                                                                                                                        | Evidence                                                                                                                              |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| **CORRECTION (plan 9d)** — the plan's "hand back for the merge" is not merely a courtesy: `permissions.deny` blocks `gh-bot.sh pr merge` and nothing allows `gh pr merge`, so **an agent cannot merge a part PR at all**. The deny misses the absolute `gh-bot.sh` spelling this skill mandates elsewhere, which is a guardrail gap to report, not a route to use (§8)                      | `settings.json:61` vs `:194`; no `GH_TOKEN=*gh pr merge*` entry                                                                       |
| **LIVE** — the whole §4-§8 chain works end to end as written: push gate → `<bot-push-script>` → `gh pr create` → `pr-finish.sh` (board Status "In progress" + **PR reviewer** requested) → `gh pr checks` exit 0 over **36** checks → human merge → `gh pr view --json state,mergedAt` confirming it. **`pr-finish.sh` skips the assignee**: GitHub refuses to assign a bot with an App installation token — expected, not a failure | plan 9d, PR #815 (`mergedAt: 2026-10-08T20:25:57Z`, merge commit `c6eb6a78…`)                                                        |
| **LIVE** — a **merged** part branch is auto-deleted on the remote (`delete_branch_on_merge: true`), so the ref `DELETE` belongs to Skill Step 6's **closed original** alone, and a `404` from it is already-done success (§8, `disposition.md` §2d)                                                                                                                                         | `gh api repos/ORG/app-main --jq '.delete_branch_on_merge'` → `true`; A's remote ref gone before cleanup                             |
| **LIVE** — `dev-build-and-deploy.yml` fires a real run on a **merge** (`in_progress`) and a `skipped` run (8s) on a plain **close**: it gates on `pull_request: closed` **and** `merged == true`. N merged parts ⇒ N staging deploys; closing the original is free (§8; DD-3 in the tracking plan)                                                                                          | runs `37839402349` (merge) vs `37839537043` (close)                                                                                   |
| **LIVE** — a **docs-only** branch takes the Push-gate seam's first row (`docs/** **/*.md … .claude/**`) and so runs **zero** suites (`"suites": [], "status": "na"`). That is what makes a docs-only throwaway the right shape for rehearsing this step in an orchestrator's own context, and the reason a docs-only part never proves anything about the suites                            | `push-gate.sh <branch>` output on `chore/split-pr-rehearsal-a`                                                                        |
| **CORRECTION** — `git checkout -b <part-branch>` is hook-denied; `make wt-new` is the only creation path, and it bases on the default branch by design                                                                                                                                                                                                                                      | `scripts/lib/branch_guard.py` (`create-ahead`); `scripts/wt.sh:326`                                                                   |
| **CORRECTION** — the push gate is a hard precondition of the push; the plan omitted it entirely                                                                                                                                                                                                                                                                                             | `gh-app-push.sh:135` and its exit code 6; `hooks/require-push-gate.sh`                                                                |
| **CORRECTION** — `gh-log-fetch.sh`'s own generator resolution **does** resolve on this machine (three tiers, ending at the tracked stronghold generator), so the plan's stated reason for forcing the prefix was wrong. Run it from `~/code` with the relative, allowlisted spelling and a mandatory `--repo`, as `pr-review-fixer` already does; keep the prefix only for a differing seam | `gh-log-fetch.sh:30-34`; `pr-review-fixer/SKILL.md:307`; `workflow-lint`'s `scripts.allowlist-coverage` rejects the absolute spelling |
| **CORRECTION** — `git-push` no longer runs the `graphql/*.graphql` files inline; the reuse happens *through* `pr-finish.sh`                                                                                                                                                                                                                                                                 | `git-push/SKILL.md:606-612`; `pr-finish.sh:83`; `lib/pr_finish.py:53-166`                                                             |
| `git diff` has no `--pathspec-from-file` (exit 129); paths are spliced literally after `--`                                                                                                                                                                                                                                                                                                 | git 2.53.0, verified in plan Step 4                                                                                                   |
| `git commit --trailer` exists; a heredoc message is blocked; repeated `-m` splits the trailer block into paragraphs                                                                                                                                                                                                                                                                         | git 2.53.0; `hooks/block-bash-writes.sh:22` (`cat` then `<<`)                                                                         |
| `git push`'s compound rule is `ops: all`, so a pipe denies it just as a chain operator does                                                                                                                                                                                                                                                                                                 | `hooks/block-compound-commands.sh:53` and its `all` op mapping                                                                        |
| `git apply` is allowlisted **bare only** and is absent from `allow-git-ops.sh`'s safe set, so a `-C` form prompts                                                                                                                                                                                                                                                                           | `settings.json:114`; `hooks/allow-git-ops.sh:99`                                                                                      |
| A bare `gh …` is hard-blocked in an app-token repo without the `GH_TOKEN=$(...)` prefix                                                                                                                                                                                                                                                                                                     | `hooks/block-unsafe-push.sh:81,241-243`                                                                                               |
| `gh pr create`, `gh pr edit`, `gh pr view`, `gh pr checks` and `gh issue create` are allowlisted in the `GH_TOKEN=` form                                                                                                                                                                                                                                                                    | `settings.json:177-184`                                                                                                               |
| `gh pr checks` exits **8** on pending and **1** on failure; `--json` adds a `bucket` field                                                                                                                                                                                                                                                                                                  | `gh pr checks --help`                                                                                                                 |
| `gh-bot.sh` resolves its App installation from the **cwd's `origin`**, not from `--repo`                                                                                                                                                                                                                                                                                                    | `gh-bot.sh:37-45`; `generate-gh-token.sh:18-19,46-57`                                                                                 |
| `~/code/.claude/scripts/gh-bot.sh` now has its own allow entry; the other seven use the relative spelling, which no sub-repo resolves                                                                                                                                                                                                                                                       | `settings.json`; `app-main/.claude/scripts/` holds no copy                                                                            |
| All nine `git-push/references/graphql/*.graphql` files exist                                                                                                                                                                                                                                                                                                                                | directory listing                                                                                                                     |
| The plan's issue number lives in its `github_issue:` frontmatter (with `github_issue_url:`)                                                                                                                                                                                                                                                                                                 | `git-push/SKILL.md:126-131`                                                                                                           |
| `block-polling-loops.sh` denies shell poll loops; a foreground `sleep` is blocked                                                                                                                                                                                                                                                                                                           | registered hook; `block-compound-commands.sh:54`                                                                                      |
| `<bot-push-script>` and `push-gate.sh` are both in `sandbox.excludedCommands`, relative **and** absolute                                                                                                                                                                                                                                                                                    | `settings.local.json:101-109`                                                                                                         |
