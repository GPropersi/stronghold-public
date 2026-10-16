# Setup

How to turn this template into a working stronghold. It assumes macOS or Linux (Windows via WSL) with
`git`, `bash`, `jq`, `python3` and [Claude Code](https://claude.com/claude-code) installed.

## 1. Wire the hooks

```bash
cp settings.example.json .claude/settings.json
```

The example registers every guard hook under `.claude/hooks/`. Add your own `permissions` block
(allow and deny rules) next to `hooks`; the allow hooks work together with it. Machine-specific values
(absolute paths, sandbox roots) belong in `.claude/settings.local.json`, which is gitignored.

## 2. Point the scripts at your repo

Several scripts and hooks refuse to run unless the stronghold's `origin` matches a fixed repo name, so
they cannot fire inside a project repo by accident. This template ships the placeholder `OWNER/stronghold`.
Replace it with your own `<owner>/<repo>`:

```bash
grep -rl 'OWNER/stronghold' .claude
```

Edit each file listed. Pushes and pull requests made through a GitHub App also expect names like
`example-bot` and `OWNER`; replace those the same way. You also need to supply your own token generator
for a GitHub App (this template does not include one) if you want bot-authored pushes and PRs. Skip that
and use your normal `git`/`gh` auth if you do not need a bot.

## 3. Add your projects

```bash
cp repos.txt.example repos.txt      # then edit it
git clone <url> ./my-project-a      # one clone per project
```

For each project:

1. Add `/my-project-a/` to `.gitignore`.
2. Give the project a `Makefile` (lint, format, test, build, dev).
3. Add a `## Claude Config` section to the project's own `CLAUDE.md`. The template is in `CLAUDE.md`.
4. Run `make workflow-lint` to check the seams and hooks.

## 4. Start working

Launch Claude Code from this directory (not from inside a project). Try `/design-doc` or `/plan-creator`
with a project and a topic. Plans appear under `plans/<project>/open/<topic>/`, and
`.claude/scripts/changelog-add.sh` keeps a dated log under `changelog/`.

See `README.md` for how the pieces fit together.
