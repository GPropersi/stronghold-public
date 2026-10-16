---
name: plan-list
description: List plans in the central stronghold store — every repo or one repo, grouped by lifecycle state (Open/Completed) with each plan's GitHub issue link. Use when asked to "list plans", "/plan-list", "what plans are open for <repo>", "show all plans", "plan status", "which plans are done", or for an at-a-glance overview across repos.
---

# Plan List

Prints plans from the **central stronghold store** (`~/code/plans/<repo>/{open,completed}/<topic>/`),
grouped by repo and then by lifecycle state (`### Open` / `### Completed`), each tagged `[x]`/`[ ]`,
annotated with its GitHub issue (`#N` or `(no issue)`), with a summary count and a trailing flat
`## Not Done` section listing every open plan. This is the one place to answer
"what plans are open for `<repo>`?" across all repos at once.

## Why a script (do not hand-roll this)

The lookup is delegated to `.claude/scripts/plan-list.sh` so the model never reads full plan files —
keeping the command both **token-cheap** and **accurate**:

- **Status comes from the folder** (`completed/` = done, `open/` = open) — the primary, robust signal.
  The `## Status` `finished:` value is read only as a secondary check: a plan filed under `completed/`
  but still marked `finished: false` is flagged as a mismatch (drift). A finished sub-plan sitting in
  `open/` because its master topic isn't fully done is expected under the topic-as-unit lifecycle and
  is **not** flagged.
- A plan counts as a **master** iff its filename ends in `-master.md`.
- The GitHub issue is read from the `github_issue:` YAML frontmatter line; `(no issue)` flags a missing
  link, not an error.
- Coverage is topic-root `.md` under `<repo>/open/` and `<repo>/completed/` only; `reviews/`,
  `research/`, `mocks/`, `screenshots/`, `tmp/` subfolders and the standalone `<repo>/research/`
  bucket are excluded by depth.

## Workflow

1. Run the scanner directly (allowlisted via `Bash(.claude/scripts/plan-list.sh:*)`, so no prompt).
   It resolves the store from its own location — **cwd does not matter**; you do NOT need to `cd` into
   any sub-repo first.

   ```
   .claude/scripts/plan-list.sh                 # every repo, grouped by repo -> Open/Completed
   .claude/scripts/plan-list.sh <repo>          # one repo (e.g. app-main, app-notifs, stronghold)
   .claude/scripts/plan-list.sh --open          # only open plans, all repos
   .claude/scripts/plan-list.sh --open <repo>   # only open plans for one repo
   ```

   Pick the scoped form when the user names a repo ("what's open for app-main?" →
   `plan-list.sh --open app-main`). Use the bare form for a cross-repo overview.

2. Relay the script's output to the user **verbatim** — it is already grouped, formatted, and
   summarized (including the trailing `## Not Done` and any `## Mismatches` section). Do **not** re-read
   individual plan files or re-derive status; the script is the source of truth.

3. If a `## Mismatches` section appears, point out that those plans sit in `completed/` but still read
   `finished: false` (or the folder/frontmatter otherwise disagree) — they likely need either a status
   flip or a move. Otherwise add nothing beyond at most a one-line summary.

Keep the response to the script output plus an optional single-line summary. Never expand into per-plan
detail unless the user explicitly asks.
