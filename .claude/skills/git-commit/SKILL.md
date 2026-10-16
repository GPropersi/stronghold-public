---
name: git-commit
description: Stage and commit changes to git in one workflow with automatic pre-commit hook handling. Use when the user asks to commit changes, create a commit, save work to git, or similar git commit requests. Automatically generates commit messages and handles pre-commit failures by fixing linting/formatting errors.
---

# Git Add and Commit

Streamline git commits by staging files, generating commit messages, and handling pre-commit hook failures automatically.

## Repo config (read first)

This skill is repo-agnostic — it reads the working repo's `## Claude Config` section (in that repo's `CLAUDE.md`; schema in the stronghold `~/code/CLAUDE.md`) for the few repo-specific values it needs:

- **Bot identity** → the `name` + `email` to author the commit as (set per-commit via `git -c`, so no global git identity is pinned across repos).
- **Container runtime** → whether pre-commit hooks need the container (decides sandbox vs `dangerouslyDisableSandbox`).
- **Default branch** → the branch the Branch Guard treats as protected (default `main`).

If a repo has no `## Claude Config` (seam not yet rolled out), fall back to the ambient git identity and commit in-sandbox first, escalating only on a sandbox-denied pre-commit hook.

## Branch Guard

**If the invoking prompt specifies a target branch** (a `WORKTREE DISCIPLINE (critical):` block naming `<plan-branch>` and `<worktree>` — e.g. when `/run-plan` or `/next-step-taker` delegates a commit): **obey it verbatim and skip the standalone flow below.** The canonical block (CLAUDE.md → Worktree Discipline for delegated subagents), exact text:

> **WORKTREE DISCIPLINE (critical):** The working branch for this plan is `<plan-branch>`, checked out at `<worktree>`. Before reading or editing any file, running a build/test, or committing, run `git -C <worktree> branch --show-current` (bare `git branch --show-current` when `<worktree>` is already the cwd, e.g. `~/code` for stronghold plans — a redundant `-C` is blocked by a hook). Bash cwd does not persist between calls: use `git -C <worktree>` for every git command and absolute paths under `<worktree>` for everything else (builds/tests: `make -C <worktree> <target>` or `--project-directory <worktree>`, never a prior `cd`). If it is **not** `<plan-branch>`, **STOP and report back** to the orchestrator. **NEVER** run `git checkout`, `git switch`, or any command that creates a branch or worktree (`git checkout -b`, `git branch <name>`, `git worktree add`) to fix it. Edit sub-repo files only by absolute path under `<worktree>`; plans, reviews and the changelog stay under `~/code/plans` and `~/code/changelog`. Whatever the reason the branch is wrong (including `<plan-branch>` being checked out in another worktree), STOP and report back; never retry, force, or create a new branch.

Otherwise (no target branch specified — standalone/ad-hoc commit): resolve `<worktree>` **once** with `~/code/.claude/scripts/wt-resolve.sh <repo-dir> --branch <branch> [--assert]` (`<branch>` = the branch of the work being committed: the plan's `branch:` when one applies, else the current branch of the checkout holding the changes; the script's header documents the rules). `<repo>` is the value of the working repo's `Plans bucket` seam key; `<repo-dir>` is the working repo's directory name (the basename of its primary clone under `~/code`), the value passed to the script and used in `~/code/<repo-dir>` paths. Consequences: `kind=none` means the branch is checked out nowhere, so run `make wt-new`; an `--assert` mismatch means STOP and report; `has no worktree support` means use the primary clone `~/code/<repo-dir>`. Use bare `git ...` instead of `git -C` when `<worktree>` is already the cwd (e.g. `~/code` for stronghold work) — a redundant `-C` is blocked by a hook. Then run the diff and the commit as `git -C <worktree> …` (Bash cwd does not persist between calls), and read the branch with `git -C <worktree> branch --show-current`; if that is empty (detached HEAD), STOP and report. Never check out or switch branches; the only creation path is `wt-new`, for standalone (non-delegated) commits only. Then:
1. If on the repo's **Default branch** (`main`/`master`) with no plan branch:
   - Do **not** switch branches. Point to the Worktree Discipline rules: a feature branch gets its own worktree, created only by `make wt-new REPO=<repo-dir> BRANCH=<branch>` from `~/code` (`has no worktree support` means that repo still uses its primary clone).
   - Ask the user (via `AskUserQuestion`), leading with the recommended option: "You're on `<default-branch>`. Create a worktree + branch for this work via `wt-new` (Recommended), or commit on `<default-branch>` as-is?" Check first whether an **existing** worktree/branch (e.g. a plan in the central store whose `branch:` frontmatter matches this work) is the intended home, and offer that instead of a duplicate.
   - Do NOT proceed until the user confirms. If they choose `wt-new`, run it standalone and re-run `wt-resolve.sh` for the path. The new worktree is based on `origin/<default-branch>`, so the uncommitted changes stay behind in the current checkout: carry them over with `git stash push -u` there, then `cd` into the new worktree and `git stash pop` (one command per Bash call) before staging.
2. If already on a feature branch: proceed normally.

This exists because a delegated commit that lands on a stray invented branch (e.g. `refactor/…` when the intended branch was `fix/…`) fragments the work — reconcile onto the intended branch, don't create one.

## Workflow

### 1. Stage Files

**Default**: Stage all changes
```bash
git add .
```

**Specific files**: If user specifies files or folders to exclude, stage only the desired files:
```bash
git add <specific-files>
```

### 2. Analyze Changes and Generate Commit Message

Before committing, run these commands in parallel to understand the changes:

```bash
~/code/.claude/scripts/git-snapshot.sh <worktree>   # one call: branch, status, staged/unstaged diffstat, recent commit style
git diff --staged                                     # only when the diffstat is not enough to write the message
```

Analyze the staged changes to craft a concise commit message (1-2 sentences) that:
- Summarizes the nature of changes (feature, fix, refactor, docs, test, etc.)
- Focuses on "why" rather than "what"
- Follows the repository's existing commit message style
- Uses appropriate prefix (add/update/fix/refactor/etc.) based on actual changes

**Skill / config file handling**: Changes under `.claude/` (skills, scripts, settings), `CLAUDE.md`, and `.gitignore` get special treatment:
- If they are mixed with other (non-config) changes: stage and commit everything together, but **ignore the config/skill files when writing the commit message** — focus the message only on the substantive changes.
- If the **only** staged changes are config/skill files: treat them as the focus and write the commit message about those updates.

### 3. Create Commit

Author the commit as the repo's **Bot identity** by passing it inline (no global git config change):

```bash
git -c user.name="<Bot identity name>" -c user.email="<Bot identity email>" commit -m "Your commit message here."
```

If the repo declares no **Bot identity**, drop the `-c` flags and commit with the ambient identity.

**Sandbox vs `dangerouslyDisableSandbox`:** if the repo's pre-commit hooks invoke its **Container runtime** (e.g. a tsc typecheck via `docker compose exec`), that needs Docker-socket access the sandbox blocks — run `git commit` with `dangerouslyDisableSandbox: true`. If the repo's pre-commit is host-only (no container step) or has no pre-commit, commit in-sandbox and escalate to DDS only if a hook fails with a sandbox `Operation not permitted`.

- Keep the git commit message to a single line.

### 4. Handle Pre-Commit Failures

If the commit fails due to pre-commit hooks:

1. **Identify the errors** - Read the pre-commit output to understand what failed (linting, formatting, etc.)

2. **Fix menial errors** - Automatically fix:
   - Code formatting issues (black, prettier, etc.)
   - Linting errors (flake8, eslint, etc.)
   - Import sorting
   - Trailing whitespace
   - Other style/formatting issues

3. **Do NOT fix** if changes would:
   - Alter business logic
   - Break functionality
   - Require architectural decisions
   - Need user input to resolve correctly

4. **Re-stage and commit** - After fixing errors, re-stage and re-run the same single-line commit (many pre-commit hooks auto-modify files, so the re-stage is required):
   ```bash
   git add .
   git -c user.name="<Bot identity name>" -c user.email="<Bot identity email>" commit -m "Your original commit message here."
   ```
   (Use a plain `-m` single-line message — never a `cat <<EOF` heredoc, which trips the brace+quote security prompt.)

5. **Report to user** - Summarize what was fixed and complete the commit

### 5. Append Changelog Entry

After a successful commit, append the changelog entry with one call (it takes the real date and time itself, creates the day's file and header, and appends `- [HH:MM] …`):

`~/code/.claude/scripts/changelog-add.sh "git-commit: <repo> <branch> <sha> - <summary>"`

Never hand-edit the changelog or assume the date from context/filenames.

## Important Notes

- Never use `--no-verify` to bypass pre-commit hooks unless explicitly requested
- If pre-commit failures require non-menial changes, report to user and ask for guidance
- Verify commit success with `git status` after completion
- **Author identity is per-repo** — set from the repo's `Bot identity` key via `git -c user.name/user.email`, so committing from the stronghold uses the correct bot for whichever sub-repo is being committed (rather than a globally pinned identity).
- **The Claude co-author trailer is governed per-repo by the harness `attribution` setting** in that repo's `.claude/settings*.json` — do NOT manually add or strip `Co-Authored-By` trailers. A repo that suppresses the trailer sets `"attribution": {"commit": ""}`; a repo that wants it leaves the default. Respect whatever the active config produces.
