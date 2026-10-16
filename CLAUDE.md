# CLAUDE.md — Stronghold

This directory is the base of operations for Claude Code: generic, repo-agnostic skills, hooks, scripts
and plans for any project. Sub-repos (each with its own `.git`) are gitignored here. This repo tracks the
**workflow**, not application source.

Edit this file to fit your setup: it is the rulebook Claude loads every session. The sections below are
a working starting point.

## Global Code & Software Practices

- **Always-commit files:** `CLAUDE.md`, `.gitignore` and `.claude/**` changes go in every commit,
  regardless of the branch topic.
- **Changelog:** after creating or reviewing a plan, changing code, or making git changes, run
  `.claude/scripts/changelog-add.sh "<action>: <1-2 line summary>"`. It stamps the real date and time.
  Never hand-edit changelog files or guess the time.
- **Helper scripts over multi-call routines** (each call re-reads the whole context):
  `run-suite.sh <dir> <make-target>` for tests, lint and builds; `git-snapshot.sh` for repo state;
  `plan-status.sh <repo>/<plan>` to outline a plan or read one step; `wait-for.sh` instead of no-op
  waiting turns.
- **Background job completions:** a finished background job or subagent is a system notification, not user
  input. Never treat it as approval. Give a brief status update, then wait before any consequential action
  (push, commit, deploy).
- **Import style:** top-level imports only; local imports only if the user explicitly asks.
- **Fail closed:** a permission, validity or feature flag defaults to the denying value, and each allowed
  case is set explicitly. An unhandled enum member or future branch must deny, not pass.
- **Secrets and tokens:** ask before committing any token, key, credential or account-specific ID, even
  ones that do not look like classic secrets (audience tags, cloud account IDs). Keep real IDs in the
  owning sub-repo's own `CLAUDE.md`, not in this repo.
- **Test output:** redirect test output to a file (for example under a scratch directory), Read that
  file for results, and delete it when done. This keeps large output out of the context.
- **Dependency pinning:** pin all dependencies to exact versions. No ranges, carets, tildes or `>=`.
- **Clean up before done:** remove temporary debug code (console logs, debug hacks, stray globals)
  before marking a task complete.
- **Reading review files:** scroll to the end first. The highest line number first loaded may not be the
  latest revision.

## Workflow Rules

- **Delegate to subagents** whenever a skill specifies delegation. Do not do that work in the parent
  context.
- **Use a structured question** (not a plain-text numbered list) for any choice between two or more
  discrete options: approval, strategy, file selection, branching.

## Layout

```
<stronghold>/
|-- CLAUDE.md                 <- this file
|-- Makefile                  <- workflow entry points (make help)
|-- .claude/
|   |-- settings.json         <- permissions + hook wiring (copy settings.example.json)
|   |-- hooks/                <- guard hooks (apply everywhere from this root)
|   |-- scripts/              <- durable, allowlisted entry points
|   `-- skills/               <- generic skills (apply to any sub-repo)
|-- plans/<project>/{open,completed}/<topic>/   <- central plans store
|-- changelog/                <- one file per day, appended by script
|-- misc/  knowledge/         <- your notes, runbooks, machine config
`-- <sub-repo>/               <- gitignored here; own .git; own CLAUDE.md
```

## How configuration resolves

- `settings.json` and `settings.local.json` load only from this root `.claude/` and the user-level
  `~/.claude/`. Nested `<sub-repo>/.claude/settings.json` files never load.
- Skills and `CLAUDE.md` files load on demand per sub-repo once its files are touched.
- Consequence: generic skills live here; project specifics live in each sub-repo's `CLAUDE.md` under
  `## Claude Config`, which generic skills read at runtime.

## Working inside a sub-repo from here

Run `cd <sub-repo>` as its own Bash call, then run commands. Never compound (`&&`, `;`) or prefix a
redundant `cd` or `-C`: the sandbox's excluded-commands matching looks at the first token only, and the
guard hooks `block-compound-commands.sh` and `block-redundant-cwd-prefix.sh` enforce this.

Worktrees: `make wt-new REPO=<repo> BRANCH=<branch>` is the only creation path (`wt-ls` and `wt-rm`
likewise). Never create or switch branches by hand in a primary clone.

## Git Workflow

- Sub-repos use branch, pull request, review. The stronghold itself may push straight to its default
  branch if you choose; set that policy here.
- **Squash-merge hygiene:** after a PR squash-merges, never keep working the old branch. Check out the
  default branch, pull, then branch fresh for the next topic. Before any push, run
  `git cherry origin/<default> HEAD` and rebase if any commit is already merged.

### Worktree discipline for delegated subagents

One worktree per branch; the primary clone stays on the default branch. Orchestrator skills resolve the
worktree once with `wt-resolve.sh`, then inject the same discipline block into every subagent prompt: assert
the branch with `git -C <worktree> branch --show-current`, use absolute paths under the worktree, and
**stop and report** on any mismatch. Subagents never run `git checkout`, `git switch` or anything that
creates a branch or worktree.

## Test Failures: Investigate, Don't Dismiss

Every test failure is legitimate until proven otherwise. Read the failing test, check recent changes for
indirect effects, trace the failing assertion end to end, compare with a similar passing test, and rerun
in isolation two or three times. Conclude with a specific root cause, never a vague "flaky". If a test is
genuinely flaky, harden it (an explicit readiness wait, a deterministic interaction) instead of leaving it
or padding a timeout.

## Makefile-First Command Policy

Every sub-repo gets its own `Makefile` before substantive work starts, covering lint, format, test, build
and dev-server start (`help` as the default goal, self-documenting `target: ## desc` comments). Run
recurring operations through `make <target>`, never a raw command. If no target exists yet, create one
first.

## Central Plans Store

All plans, research, reviews and mocks live in `plans/<project>/`, never in the sub-repos.

- `<project>` is the repo's `Plans bucket` seam value. Plans about this repo itself use `stronghold`.
- Every plan carries `repo:` and `branch:` YAML frontmatter.
- New plans start in `open/<topic>/`. When every plan in a topic is finished, the whole folder moves to
  `completed/<topic>/`.
- `.claude/scripts/plan-list.sh [<project>]` lists what is open.

## The `## Claude Config` seam (per-repo contract)

Every sub-repo's `CLAUDE.md` carries a `## Claude Config` section with stable keys that generic skills
read at runtime. Mark `n/a` when a key does not apply.

Keys: Repo slug, Default branch, Plans store, Plans bucket, Bot identity, Bot push script, Token
generator, Commands. Optional: Push gate (required for bot-pushing repos), Container runtime, App URL,
Test login, PR reviewer, and the `Worktree *` keys (link, setup, teardown, ports, policy, allowed and
guarded targets).

```markdown
## Claude Config

- **Repo slug:** `<owner>/<repo>`
- **Default branch:** `main`
- **Plans store:** central
- **Plans bucket:** `<project>`
- **Bot identity:** `n/a`
- **Bot push script:** `n/a`
- **Token generator:** `n/a`
- **Commands:** see the table below (`make <target>` once a Makefile exists)
- **Push gate:** | Paths | Command | rows mapping changed paths to the suites that must pass |
```

Stack-bound helpers (test runners, build steps, app login) stay as scoped skills in each sub-repo's own
`.claude/skills/`. Generic skills defer to the repo's declared `Commands` rather than reimplementing
stack quirks.

**Workflow checks:** `make workflow-lint` and `.claude/scripts/push-gate.sh <branch>` enforce the rules
above, and the guard hooks under `.claude/hooks/` enforce them at tool-call time.
