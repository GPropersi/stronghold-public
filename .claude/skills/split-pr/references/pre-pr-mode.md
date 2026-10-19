# Pre-PR mode (handoff from `/git-push` Step 7e)

`/git-push` Step 7e measures the **final** diff after its reviews (Steps 3-7c) and push gate (7d).
When it is over the size ceiling and the user chooses to split, `/git-push` stops before its own
push and hands off here with `/split-pr <branch> --pre-pr`. The branch has no PR and no CI run, but
it is fully reviewed and green locally at HEAD. The parts then ship **one at a time** through the
normal Steps 4-5 loop, each squash-merged before the next is cut: no stacking, no merge commits, no
force push, and the default branch's history stays one squash commit per PR.

**Why no re-review.** A split only moves already-reviewed code: the slice planner never synthesizes
content (no `--3way`, no invented files, straddlers resolved by assigning or splitting existing
hunks), and Skill Step 3e aborts unless the merged parts reproduce the original tree hash exactly.
Every line in every part is a line `/git-push` already reviewed. What a split *can* break is
greenness (a part missing something an earlier or later part carries), and that is re-proved per
part, unchanged from normal mode: Skill Step 4's prefix gate (one built/full-stack run per part,
with parts 1…*k-1* merged in), Step 5c's `push-gate.sh` on each part's own HEAD, and Step 5g's CI on
each part PR. Any gate failure STOPS the split (4d).

## Handoff file (`--handoff <file>`)

`/git-push` Step 7e writes `<plan-dir>/split/split-handoff.md` so this skill can start in a new
session that has none of the push session's context. When `--handoff` is given:

1. **Read the whole file first.** It supplies `<branch>` (so no branch argument is needed),
   `<repo-dir>`, the worktree, the head SHA, the base, the plan and issue, and the gate and
   push-review evidence. `cd ~/code/<repo-dir>` as its own Bash call before anything else (the
   session starts in `~/code`).
2. **Treat it as claims to verify, not evidence.** The Entry check below still runs unchanged:
   `git rev-parse <branch>` must equal the file's head SHA, and `push-gate.sh --status <branch>` must
   show `pass`/`na` for that SHA. A mismatch means a commit landed after `/git-push` (or the file is
   stale): STOP and route back to `/git-push`.
3. **Themes and part boundaries are advisory input** to the slice planner (Skill Step 2); the planner
   still derives and justifies the split itself.
4. **A `split-manifest.md` already beside the handoff means resume** (Skill Step 1b), not a fresh
   start; the handoff file is then only context.

## Entry check (replaces Skill Steps 1a and 1c)

1. **No PR may exist.** `GH_TOKEN=$(<token-generator>) gh pr view <branch>` must report no PR. If one
   exists, drop `--pre-pr` and run normal mode (green CI gate).
2. **Record the head SHA.** `git rev-parse <branch>`; note the literal 40 characters (never a shell
   variable, per Skill Step 1d).
3. **The push gate stands in for green CI.** `~/code/.claude/scripts/push-gate.sh --status <branch>`
   must show status `pass` (or `na`) **for exactly that head SHA**. Any other status, a different SHA
   (a commit landed after the gate) or a missing result file: **STOP** and route back to `/git-push`.
   Never run the gate from here to manufacture the evidence: the gate result is only meaningful as
   the end of `/git-push`'s review, which is what makes the code reviewed.

Skill Step 1b (resume) and 1d (pin the base) run unchanged.

## Provenance (Skill Step 1e deltas)

Same five artifacts, so 1f's and `disposition.md` §2's `test -s` checks hold unchanged:

| Artifact                | Pre-PR content                                                                                                                                                                                             |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `original-series.patch` | unchanged                                                                                                                                                                                                  |
| `original-log.txt`      | unchanged                                                                                                                                                                                                  |
| `review-threads.md`     | the push-review record instead of PR threads: the `/git-push` review file path under `<plan-dir>/reviews/`, its final verdict and round count, or the literal `no findings: no review file (git-push 4b)` |
| `original-pr.md`        | `none: pre-PR split from /git-push Step 7e`, the branch, head SHA, pinned literal `BASE`, and the push-gate result (file path, status, suites with counts) in place of check-run IDs                       |

## Later step deltas

- **Steps 2, 3, 4, 5a-5c, 5f-5i:** unchanged.
- **5d (PR body):** replace `Split from #<original-PR#>` with `Split from branch <branch> (pre-PR)`,
  link the push-review file, and add one line: reviewed as one unit by `/git-push` before the split;
  this part holds only reviewed hunks (union tree hash matched in Step 3e). Everything else in
  `pr-loop.md` §5 holds.
- **5e (issue):** with no issue in the plan frontmatter (`github_issue`), create one once, before
  part 1, the way `/git-push` Step 2d would; then wire `Part of` / `Closes` as normal.
- **6a:** skip: there is no original PR to cross-link or close. Each part body already links the
  siblings and the push review.
- **6b:** the original branch was never pushed. Archive re-check and your own `AskUserQuestion` still
  gate the local `git branch -D`; skip the remote `refs/heads` DELETE unless
  `git ls-remote --heads origin <branch>` shows the ref.
- **6c, 6d:** unchanged.
