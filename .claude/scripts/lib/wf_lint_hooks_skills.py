"""Hooks and skills groups of the workflow lint: settings.json wiring and skill text rules."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import wf_lint_base as base

HOOK_PATH_RE = re.compile(r"\.claude/hooks/([A-Za-z0-9_.-]+\.sh)")
SCRIPT_CITE_RE = re.compile(r"(?<![\w./~$-])(?:~/code/)?\.claude/scripts/[A-Za-z0-9_./-]+\.(?:sh|py)\b")
RELATIVE_SKILL_RE = re.compile(r"(?<![\w./~$-])\.claude/skills/[\w./-]+")
SCRIPTS_PREFIX = ".claude/scripts/"


def _json(path: Path) -> dict:
    try:
        loaded = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _settings(ctx: base.Context) -> dict:
    return _json(ctx.root / ".claude" / "settings.json")


def _hook_commands(settings: dict) -> list[str]:
    commands: list[str] = []
    for group in (settings.get("hooks") or {}).values():
        for entry in group if isinstance(group, list) else []:
            for hook in entry.get("hooks", []) if isinstance(entry, dict) else []:
                if isinstance(hook, dict) and isinstance(hook.get("command"), str):
                    commands.append(hook["command"])
    return commands


def _perm(settings: dict, kind: str) -> list[str]:
    values = (settings.get("permissions") or {}).get(kind) or []
    return [v for v in values if isinstance(v, str)]


def run_hooks(ctx: base.Context) -> list[base.Finding]:
    out: list[base.Finding] = []
    settings = _settings(ctx)
    commands = _hook_commands(settings)
    hooks_dir = ctx.root / ".claude" / "hooks"
    registered = {m.group(1) for c in commands for m in [HOOK_PATH_RE.search(c)] if m}
    on_disk = sorted(p.name for p in hooks_dir.glob("*.sh")) if hooks_dir.is_dir() else []
    allowed = {e.get("hook"): e for e in ctx.cfg["unregistered"] if isinstance(e, dict)}
    for name in on_disk:
        location = f".claude/hooks/{name}"
        if name not in registered and name not in allowed:
            out.append(base.Finding("error", "hooks.all-registered", location,
                                    "hook is not registered in settings.json (and not in the `unregistered` allowlist)"))
    for name in sorted(allowed):
        if name in registered:
            out.append(base.Finding("warn", "hooks.all-registered", f".claude/hooks/{name}",
                                    "stale `unregistered` entry: the hook is registered now (remove the entry)"))
    for command in commands:
        found = HOOK_PATH_RE.search(command)
        if not found:
            continue
        name = found.group(1)
        path = hooks_dir / name
        if not path.is_file():
            out.append(base.Finding("error", "hooks.registered-exists-and-exec", f".claude/hooks/{name}",
                                    "registered hook file does not exist"))
            continue
        direct = command.strip().startswith(('"$CLAUDE_PROJECT_DIR', "$CLAUDE_PROJECT_DIR"))
        if direct and not os.access(path, os.X_OK):
            out.append(base.Finding("error", "hooks.registered-exists-and-exec", f".claude/hooks/{name}",
                                    "hook is registered for direct execution but is not executable (chmod +x)"))
    deny = set(_perm(settings, "deny"))
    pending = set(ctx.cfg["pending_deny"])
    for guard in ctx.cfg["guard"]:
        line = f"Edit(~/code/.claude/{guard})"
        location = f".claude/{guard}"
        if line not in deny:
            severity = "warn" if guard in pending else "error"
            out.append(base.Finding(severity, "hooks.deny-edit-coverage", location, f"no `{line}` deny line in settings.json"))
        elif guard in pending:
            out.append(base.Finding("warn", "hooks.deny-edit-coverage", location,
                                    "stale `pending_deny` entry: the deny line exists (remove the entry)"))
    for name in ctx.cfg["both_forms"]:
        path = ctx.root / ".claude" / "scripts" / name
        location = f".claude/scripts/{name}"
        if not path.is_file():
            out.append(base.Finding("error", "scripts.exec-bit", location, "script named in both_forms does not exist"))
        elif not os.access(path, os.X_OK):
            out.append(base.Finding("error", "scripts.exec-bit", location, "script is not executable (chmod +x)"))
    out.extend(_excluded_pairs(ctx))
    return out


def _excluded_pairs(ctx: base.Context) -> list[base.Finding]:
    template = _json(ctx.root / ".claude" / "settings.local.template.json")
    entries = [e for e in ((template.get("sandbox") or {}).get("excludedCommands") or []) if isinstance(e, str)]
    exempt = set(ctx.cfg["excluded_exempt"])
    have = set(entries)
    out: list[base.Finding] = []
    for entry in entries:
        for prefix in ("__STRONGHOLD__/", "~/code/"):
            if entry.startswith(prefix):
                rel = entry[len(prefix) :]
                break
        else:
            rel = entry
        if not rel.startswith(SCRIPTS_PREFIX) or rel in exempt or entry in exempt:
            continue
        for needed in (rel, "__STRONGHOLD__/" + rel):
            if needed not in have and needed not in exempt:
                out.append(base.Finding("error", "scripts.excluded-pairs", ".claude/settings.local.template.json",
                                        f"excludedCommands has `{entry}` but lacks `{needed}`"))
    return sorted(set(out), key=lambda f: f.sort_key())


def _skill_files(ctx: base.Context) -> list[Path]:
    root = ctx.root / ".claude" / "skills"
    return sorted(root.glob("*/**/*.md")) if root.is_dir() else []


def _skill_name(ctx: base.Context, file: Path) -> str:
    return file.relative_to(ctx.root / ".claude" / "skills").parts[0]


def _read(file: Path) -> list[str]:
    try:
        return file.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return []


def citations(lines: list[str]) -> list[tuple[int, str, bool]]:
    """(line, token, is_python) for each `.claude/scripts/*.{sh,py}` token inside an inline code
    span or a fenced block; prose text is never a citation."""
    found: list[tuple[int, str, bool]] = []
    fence = False
    for number, line in enumerate(lines, start=1):
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        segments = [line] if fence else re.findall(r"`([^`]+)`", line)
        for segment in segments:
            for match in SCRIPT_CITE_RE.finditer(segment):
                is_python = re.search(r"python3\s+$", segment[: match.start()]) is not None
                found.append((number, match.group(0), is_python))
    return found


def _rel_script(token: str) -> str:
    return token.split(SCRIPTS_PREFIX, 1)[1]


def _allowlist_checks(ctx: base.Context) -> list[base.Finding]:
    out: list[base.Finding] = []
    allow = [a for a in _perm(_settings(ctx), "allow") if a.startswith("Bash(")]

    def covered(prefix: str) -> bool:
        return any(a.startswith(f"Bash({prefix}:") or a.startswith(f"Bash({prefix} ") for a in allow)

    exempt = {e.get("script") for e in ctx.cfg["allowlist_exempt"] if isinstance(e, dict)}
    for file in _skill_files(ctx):
        for number, token, is_python in citations(_read(file)):
            rel = _rel_script(token)
            location = f"{ctx.rel(file)}:{number}"
            if rel.rsplit("/", 1)[-1] in exempt:
                continue
            if is_python:
                if rel not in ctx.cfg["python_invoked"]:
                    out.append(base.Finding("error", "scripts.allowlist-coverage", location,
                                            f"`python3 {token}` cites a script missing from seam-keys.json python_invoked"))
                elif not covered(f"python3 {token}"):
                    out.append(base.Finding("error", "scripts.allowlist-coverage", location,
                                            f"no `Bash(python3 {token}:*)` allow rule"))
                continue
            needed = [token]
            if rel in ctx.cfg["both_forms"]:
                needed = [SCRIPTS_PREFIX + rel, "~/code/" + SCRIPTS_PREFIX + rel]
            for spelling in needed:
                if not covered(spelling):
                    out.append(base.Finding("error", "scripts.allowlist-coverage", location,
                                            f"no `Bash({spelling}:*)` allow rule in settings.json"))
    return out


def _placeholder_checks(ctx: base.Context) -> list[base.Finding]:
    out: list[base.Finding] = []
    for spec in ctx.cfg["skill_placeholders"]:
        name, defined_by = spec["name"], re.compile(spec["defined_by"])
        for file in _skill_files(ctx):
            if file.name != "SKILL.md":
                continue
            lines = _read(file)
            first_use = next((i for i, line in enumerate(lines) if name in line), None)
            if first_use is None:
                continue
            defined = next((i for i, line in enumerate(lines) if defined_by.search(line)), None)
            location = f"{ctx.rel(file)}:{first_use + 1}"
            if defined is None:
                out.append(base.Finding("error", "skills.placeholder-defined-before-use", location,
                                        f"{name} is used but never defined"))
            elif first_use < defined:
                out.append(base.Finding("error", "skills.placeholder-defined-before-use", location,
                                        f"{name} is used on line {first_use + 1} before it is defined on line {defined + 1}"))
    return out


def _relative_path_checks(ctx: base.Context) -> list[base.Finding]:
    out: list[base.Finding] = []
    for file in _skill_files(ctx):
        if _skill_name(ctx, file) not in ctx.cfg["relative_path_skills"]:
            continue
        for number, line in enumerate(_read(file), start=1):
            for match in RELATIVE_SKILL_RE.finditer(line):
                out.append(base.Finding("error", "skills.relative-skill-paths", f"{ctx.rel(file)}:{number}",
                                        f"relative path `{match.group(0)}` does not resolve from a sub-repo cwd (use ~/code/...)"))
    return out


def _script_exists_checks(ctx: base.Context) -> list[base.Finding]:
    out: list[base.Finding] = []
    for file in _skill_files(ctx):
        for number, token, _ in citations(_read(file)):
            target = ctx.root / SCRIPTS_PREFIX / _rel_script(token)
            if not target.is_file():
                out.append(base.Finding("error", "skills.script-paths-exist", f"{ctx.rel(file)}:{number}",
                                        f"cited script `{token}` does not exist"))
    return out


def run_skills(ctx: base.Context) -> list[base.Finding]:
    out: list[base.Finding] = []
    for check in (_allowlist_checks, _placeholder_checks, _relative_path_checks, _script_exists_checks):
        out.extend(check(ctx))
    return out
