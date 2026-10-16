"""Seam group of the workflow lint: the `## Claude Config` contract of every sub-repo CLAUDE.md."""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

import seam_push_gate
import wf_lint_base as base

ABSOLUTE_HOME_RE = re.compile(r"/Users/[^/\s`]+|/home/[^/\s`]+")
CENTRAL_BOT_SCRIPT = ".claude/scripts/gh-app-push.sh"
CANONICAL_IDENTITY = ["example-bot[bot]", "12345678+example-bot[bot]@users.noreply.github.com"]
TARGET_RE = re.compile(r"^[A-Za-z0-9_.][A-Za-z0-9_.-]*$")
MAKE_ARG_FLAGS = {"-C", "-f", "-I", "-j", "-o", "-W", "--directory", "--file", "--jobs"}


def _loc(repo: str, line: int = 0) -> str:
    return f"{repo}/CLAUDE.md:{line}" if line else f"{repo}/CLAUDE.md"


def _bot_repos(ctx: base.Context) -> list[str]:
    return [r for r in ctx.scoped_repos() if ctx.is_bot_repo(r)]


def make_targets(makefile: Path) -> set[str]:
    """Target names declared in a Makefile (`a b: ...` grouped targets, not `X := y` assignments)."""
    try:
        text = makefile.read_text()
    except (OSError, UnicodeDecodeError):
        return set()
    names: set[str] = set()
    for line in text.splitlines():
        if not line or line[0] in "\t #" or "=" in line.split(":", 1)[0]:
            continue
        head, sep, rest = line.partition(":")
        if not sep or rest.startswith("="):
            continue
        names.update(head.split())
    return names


def make_invocations(command: str) -> list[str | None]:
    """The target of each plain `make <target>` in a shell command; None for `-C`/`-f` forms."""
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    out: list[str | None] = []
    for index, word in enumerate(words):
        if word != "make":
            continue
        target: str | None = None
        redirected = False
        skip = False
        for nxt in words[index + 1 :]:
            if skip:
                skip = False
                continue
            if nxt in ("&&", "||", ";", "|"):
                break
            if nxt in MAKE_ARG_FLAGS:
                redirected = True
                skip = True
                continue
            if nxt.startswith("-") or "=" in nxt:
                continue
            target = nxt
            break
        if redirected:
            out.append(None)
        elif target is not None and TARGET_RE.match(target):
            out.append(target)
    return out


def _referenced_targets(commands: list[str]) -> list[str]:
    return [t for c in commands for t in make_invocations(c) if t]


def _section_present(ctx: base.Context) -> list[base.Finding]:
    out = []
    for repo in ctx.scoped_repos():
        if ctx.is_exempt(repo):
            continue
        if not ctx.seam(repo).has_section:
            out.append(base.Finding("error", "seam.section-present", _loc(repo),
                                    "CLAUDE.md missing or has no column-0 `## Claude Config` section", repo))
    return out


def _required_keys(ctx: base.Context) -> list[base.Finding]:
    out = []
    legacy = ctx.cfg["legacy"]
    for repo in ctx.scoped_repos():
        seam = ctx.seam(repo)
        if ctx.is_exempt(repo) or not seam.has_section:
            continue
        keys = set(seam.keys())
        for key in ctx.cfg["required"]:
            if key == "Plans bucket" or key in keys:
                continue
            if any(old in keys and new == key for old, new in legacy.items()):
                continue
            out.append(base.Finding("error", "seam.required-keys-present", _loc(repo),
                                    f'missing required seam key "{key}"', repo))
    return out


def _exempt_no_bot(ctx: base.Context) -> list[base.Finding]:
    out = []
    for repo in ctx.scoped_repos():
        if ctx.is_exempt(repo) and ctx.seam(repo).token("Bot push script"):
            out.append(base.Finding("error", "seam.exempt-has-no-bot-script", _loc(repo),
                                    "an exempt repo must declare `Bot push script: n/a` (the hook and gate apply to bot-pushing repos)", repo))
    return out


def _indented_and_unknown(ctx: base.Context) -> list[base.Finding]:
    out = []
    known = set(ctx.cfg["required"]) | set(ctx.cfg["optional"]) | set(ctx.cfg["legacy"])
    nested = re.compile(r"^\s*- \*\*([^*]+):\*\*")
    for repo in ctx.scoped_repos():
        seam = ctx.seam(repo)
        if ctx.is_exempt(repo) or not seam.has_section:
            continue
        valid_lines = {n for _, _, n in seam.entries}
        start = seam.section[0][0]
        fence = False
        for number, line in seam.all_lines:
            if line.lstrip().startswith("```"):
                fence = not fence
                continue
            if number <= start:
                continue
            if line.startswith("## ") and not fence:
                break
            found = nested.match(line)
            if found and number not in valid_lines and found.group(1) in known | set(ctx.cfg["worktree"]):
                out.append(base.Finding("error", "seam.indented-key", _loc(repo, number),
                                        f'key "{found.group(1)}" is indented or fenced, so every reader ignores it', repo))
        for key, _, number in seam.entries:
            if key in ctx.cfg["legacy"]:
                out.append(base.Finding("warn", "seam.unknown-key", _loc(repo, number),
                                        f'legacy key "{key}": rename it to "{ctx.cfg["legacy"][key]}"', repo))
            elif key not in known:
                out.append(base.Finding("warn", "seam.unknown-key", _loc(repo, number),
                                        f'unknown seam key "{key}" (not in .claude/seam-keys.json)', repo))
    return out


def _bot_identity(ctx: base.Context) -> list[base.Finding]:
    out = []
    for repo in ctx.scoped_repos():
        seam = ctx.seam(repo)
        value = seam.value("Bot identity")
        if ctx.is_exempt(repo) or value is None or value.startswith("n/a") or value.strip("`") == "n/a":
            continue
        tokens = re.findall(r"`([^`]*)`", value)
        if tokens != CANONICAL_IDENTITY:
            out.append(base.Finding("error", "seam.bot-identity-canonical", _loc(repo, seam.line("Bot identity")),
                                    "Bot identity must be exactly `example-bot[bot]` plus the noreply email, or n/a", repo))
    return out


def _resolve_path(ctx: base.Context, repo: str, token: str) -> Path:
    expanded = base.expand_home(token, ctx.home)
    path = Path(expanded)
    return path if path.is_absolute() else ctx.root / repo / path


def _executable_resolves(ctx: base.Context, key: str, check_id: str) -> list[base.Finding]:
    out = []
    for repo in ctx.scoped_repos():
        seam = ctx.seam(repo)
        token = seam.token(key)
        if ctx.is_exempt(repo) or not token:
            continue
        path = _resolve_path(ctx, repo, token)
        if not path.is_file():
            problem = f"{key} `{token}` does not exist ({path})"
        elif not os.access(path, os.X_OK):
            problem = f"{key} `{token}` is not executable ({path})"
        else:
            continue
        out.append(base.Finding("error", check_id, _loc(repo, seam.line(key)), problem, repo))
    return out


def _bot_central(ctx: base.Context) -> list[base.Finding]:
    out = []
    for repo in _bot_repos(ctx):
        seam = ctx.seam(repo)
        token = seam.token("Bot push script")
        central = ctx.root / CENTRAL_BOT_SCRIPT
        if token in ("~/code/" + CENTRAL_BOT_SCRIPT, "$HOME/code/" + CENTRAL_BOT_SCRIPT, "${HOME}/code/" + CENTRAL_BOT_SCRIPT):
            continue
        if Path(base.expand_home(token, ctx.home)) == central:
            continue
        out.append(base.Finding("error", "seam.bot-push-script-central", _loc(repo, seam.line("Bot push script")),
                                f"Bot push script `{token}` must be the central ~/code/{CENTRAL_BOT_SCRIPT}", repo))
    return out


def _pr_reviewer(ctx: base.Context) -> list[base.Finding]:
    out = []
    for repo in _bot_repos(ctx):
        seam = ctx.seam(repo)
        if seam.token("PR reviewer") != "OWNER":
            out.append(base.Finding("error", "seam.pr-reviewer", _loc(repo, seam.line("PR reviewer")),
                                    "PR reviewer must be `OWNER` for every bot-pushing repo", repo))
    return out


def _template(ctx: base.Context) -> list[base.Finding]:
    template = ctx.cfg.get("template", "")
    in_template = set(re.findall(r"- \*\*([^*]+):\*\*", template))
    wanted = set(ctx.cfg["required"]) | set(ctx.cfg["optional"])
    missing = sorted(wanted - in_template)
    extra = sorted(in_template - wanted)
    out = []
    location = ".claude/seam-keys.json"
    if missing:
        out.append(base.Finding("error", "seam.template-matches-keys", location,
                                "template lacks keys: " + ", ".join(missing)))
    if extra:
        out.append(base.Finding("error", "seam.template-matches-keys", location,
                                "template has keys outside required/optional: " + ", ".join(extra)))
    return out


def _git_value(repo_dir: Path, *args: str) -> str | None:
    code, out = base.git(repo_dir, *args)
    return out.strip() if code == 0 and out.strip() else None


def _origin_checks(ctx: base.Context) -> list[base.Finding]:
    out = []
    for repo in ctx.scoped_repos():
        seam = ctx.seam(repo)
        if ctx.is_exempt(repo) or not seam.has_section:
            continue
        repo_dir = ctx.root / repo
        default = seam.token("Default branch")
        head = _git_value(repo_dir, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
        if default:
            location = _loc(repo, seam.line("Default branch"))
            if head is None:
                out.append(base.Finding("warn", "seam.default-branch-matches-origin", location,
                                        "origin/HEAD cannot be read (run `git remote set-head origin -a`)", repo))
            elif head.split("/", 1)[-1] != default:
                out.append(base.Finding("error", "seam.default-branch-matches-origin", location,
                                        f"Default branch `{default}` differs from origin/HEAD `{head}`", repo))
        slug = seam.token("Repo slug")
        if slug:
            location = _loc(repo, seam.line("Repo slug"))
            url = _git_value(repo_dir, "remote", "get-url", "origin")
            tail = re.sub(r"\.git$", "", (url or "").rstrip("/"))
            tail = re.split(r"[:/]", tail)[-2:] if tail else []
            if url is None:
                out.append(base.Finding("warn", "seam.slug-matches-origin-url", location, "origin URL cannot be read", repo))
            elif "/".join(tail).lower() != slug.lower():
                out.append(base.Finding("error", "seam.slug-matches-origin-url", location,
                                        f"Repo slug `{slug}` does not match origin `{url}`", repo))
            if slug.split("/")[-1] != repo:
                out.append(base.Finding("info", "seam.slug-dir-mismatch", location,
                                        f"slug basename `{slug.split('/')[-1]}` differs from directory `{repo}` (harmless: the bucket is explicit)", repo))
    return out


def _plans_bucket(ctx: base.Context) -> list[base.Finding]:
    out = []
    fixed = set(ctx.cfg["fixed_buckets"].values())
    declared = ctx.declared_buckets()
    for repo in ctx.scoped_repos():
        seam = ctx.seam(repo)
        if ctx.is_exempt(repo) or not seam.has_section:
            continue
        bucket = ctx.bucket_of(repo)
        if not bucket:
            out.append(base.Finding("error", "seam.plans-bucket-present", _loc(repo),
                                    'missing seam key "Plans bucket"', repo))
            continue
        location = _loc(repo, seam.line("Plans bucket"))
        if not (ctx.root / "plans" / bucket).is_dir():
            out.append(base.Finding("info", "seam.plans-bucket-dir-exists", location,
                                    f"plans/{bucket}/ does not exist yet (the repo has no plans)", repo))
        if len(declared.get(bucket, [])) > 1:
            others = ", ".join(sorted(declared[bucket]))
            out.append(base.Finding("error", "seam.plans-bucket-unique", location,
                                    f"bucket `{bucket}` is declared by several repos: {others}", repo))
        elif bucket in fixed:
            out.append(base.Finding("error", "seam.plans-bucket-unique", location,
                                    f"bucket `{bucket}` is reserved for the stronghold", repo))
        for key in ("Plans store (central)", *(k for k, v in ctx.cfg["legacy"].items() if v == "Plans store (central)")):
            value = seam.value(key)
            if value and not re.search(rf"plans/{re.escape(bucket)}(?=[/\s`'\"}})]|$)", value):
                out.append(base.Finding("error", "seam.plans-store-consistent", _loc(repo, seam.line(key)),
                                        f'"{key}" does not contain plans/{bucket}/', repo))
    return out


def _push_gate(ctx: base.Context) -> list[base.Finding]:
    out = []
    for repo in _bot_repos(ctx):
        seam = ctx.seam(repo)
        if not seam.has("Push gate"):
            out.append(base.Finding("error", "seam.push-gate-present", _loc(repo),
                                    'missing seam key "Push gate" (every bot-pushing repo declares one)', repo))
            continue
        parsed = seam_push_gate.parse(seam.path)
        for error in parsed["errors"]:
            out.append(base.Finding("error", "seam.push-gate-rows-valid", _loc(repo, error["line"]), error["message"], repo))
        targets = make_targets(ctx.root / repo / "Makefile")
        for row in parsed["rows"]:
            for target in _referenced_targets([row["command"]] if row["command"] else []):
                if target not in targets:
                    out.append(base.Finding("error", "seam.push-gate-rows-valid", _loc(repo, row["line"]),
                                            f"make target `{target}` is not in {repo}/Makefile", repo))
    return out


def _commands(ctx: base.Context) -> list[base.Finding]:
    out = []
    for repo in ctx.scoped_repos():
        seam = ctx.seam(repo)
        if ctx.is_exempt(repo) or not seam.has("Commands"):
            continue
        targets = make_targets(ctx.root / repo / "Makefile")
        for number, line in seam.block("Commands") + [(seam.line("Commands"), seam.value("Commands") or "")]:
            for cell in re.findall(r"`([^`]*)`", line):
                for target in _referenced_targets([cell]):
                    if target not in targets:
                        out.append(base.Finding("error", "seam.commands-targets-exist", _loc(repo, number),
                                                f"`make {target}` is not a target in {repo}/Makefile", repo))
    return out


def _stale_home(ctx: base.Context) -> list[base.Finding]:
    out = []
    for repo in ctx.scoped_repos():
        seam = ctx.seam(repo)
        if ctx.is_exempt(repo):
            continue
        for number, line in seam.section:
            found = ABSOLUTE_HOME_RE.search(line)
            if found:
                out.append(base.Finding("error", "seam.stale-absolute-home", _loc(repo, number),
                                        f"absolute home path `{found.group(0)}` in the seam (use ~ or a relative path)", repo))
    return out


def run(ctx: base.Context) -> list[base.Finding]:
    findings: list[base.Finding] = []
    for check in (
        _section_present,
        _required_keys,
        _exempt_no_bot,
        _indented_and_unknown,
        _bot_identity,
        _bot_central,
        _pr_reviewer,
        _origin_checks,
        _plans_bucket,
        _push_gate,
        _commands,
        _stale_home,
    ):
        findings.extend(check(ctx))
    findings.extend(_executable_resolves(ctx, "Bot push script", "seam.bot-push-script-resolves"))
    findings.extend(_executable_resolves(ctx, "Token generator", "seam.token-generator-resolves"))
    if ctx.repo_filter is None:
        findings.extend(_template(ctx))
    return findings
