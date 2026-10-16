#!/usr/bin/env python3
"""Segment-wise verdict for the compound-command guard (block-compound-commands.sh).

Reads one Bash command on stdin and prints exactly one line:

    PASS            the command is not a compound, or every top-level segment would be
                    auto-allowed if sent as its own Bash call
    DENY: <reason>  keep the guard's blanket denial (the reason names the blocking segment)

PASS only means "the compound guard has no objection"; every other hook and the normal permission
flow still evaluate the full command afterwards. The decision is deliberately conservative and fails
closed: anything unusual (command substitution, heredocs, background jobs, grouping, process
substitution, ANSI-C quoting, unbalanced quotes, too many segments, any internal error) is DENY.

A segment counts as auto-allowed on its own when it is a plain `cd <path>`, matches a settings.json
`Bash(...)` allow rule (and no deny rule), or is approved by allow-readonly-bash.sh or
allow-git-ops.sh. allow-safe-entrypoints.sh and allow-tmp-access.sh are NOT trusted here: the first
decides on the first word only and the second on a substring, so neither is evidence about a whole
segment. Segments that start a make/docker/git-push command are always refused (their own guard rules
in block-compound-commands.sh run first; this is defence in depth).

Documentation, rollout and revert steps: misc/compound-guard-segmentwise.md

Env (test seams): COMPOUND_GUARD_HOOKS_DIR (default: <stronghold>/.claude/hooks),
COMPOUND_GUARD_SETTINGS (os.pathsep-separated settings files, default: settings.json and settings.local.json)
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path

STRONGHOLD = Path(__file__).resolve().parents[3]
HOOKS_DIR = Path(os.environ.get("COMPOUND_GUARD_HOOKS_DIR", STRONGHOLD / ".claude" / "hooks"))
SETTINGS_FILES = [
    Path(p)
    for p in os.environ.get(
        "COMPOUND_GUARD_SETTINGS",
        os.pathsep.join(str(STRONGHOLD / ".claude" / name) for name in ("settings.json", "settings.local.json")),
    ).split(os.pathsep)
    if p
]
MAX_SEGMENTS = 12
MAX_LENGTH = 20000
HOOK_TIMEOUT_SECONDS = 5

SPECIAL_START = re.compile(r"^(?:\w+=\S*\s+)*(?:make|docker)(?:\s|$)")
GIT_PUSH = re.compile(r"^(?:\w+=\S*\s+)*git\s+(?:-\S+(?:\s+\S+)?\s+)*push(?:\s|$)")
PLAIN_CD = re.compile(r"""^cd\s+(?:"[^"$`\\]+"|'[^']+'|[^\s|&;$`<>(){}*?\\]+)$""")
GROUPING_START = re.compile(r"^[({]")


class Unusual(Exception):
    """The command uses a construct this guard refuses to reason about."""


def split_top_level(cmd: str) -> list[str]:
    """Split on top-level `&&`, `||`, `;` and newlines. Quote- and escape-aware; pipes stay inside a segment."""
    segments: list[str] = []
    cur: list[str] = []
    single = double = False
    i, n = 0, len(cmd)
    while i < n:
        ch = cmd[i]
        two = cmd[i : i + 2]
        if single:
            cur.append(ch)
            single = ch != "'"
        elif double:
            if ch == "\\" and i + 1 < n:
                cur.append(cmd[i : i + 2])
                i += 2
                continue
            if ch == "`" or two == "$(":
                raise Unusual("command substitution")
            cur.append(ch)
            double = ch != '"'
        else:
            if ch == "\\" and i + 1 < n:
                cur.append(cmd[i : i + 2])
                i += 2
                continue
            if ch == "'":
                single = True
                cur.append(ch)
            elif ch == '"':
                double = True
                cur.append(ch)
            elif ch == "`" or two == "$(":
                raise Unusual("command substitution")
            elif two == "$'":
                raise Unusual("ANSI-C quoting")
            elif two in ("<<", "<(", ">("):
                raise Unusual("heredoc, here-string or process substitution")
            elif two in ("&&", "||"):
                segments.append("".join(cur))
                cur = []
                i += 2
                continue
            elif ch in ";\n":
                if two == ";;":
                    raise Unusual("case terminator")
                segments.append("".join(cur))
                cur = []
            elif ch == "&":
                prev = cmd[i - 1] if i else ""
                nxt = cmd[i + 1] if i + 1 < n else ""
                if prev in (">", "|") or nxt == ">":
                    cur.append(ch)  # 2>&1, >&2, &>file, |&
                else:
                    raise Unusual("background job")
            else:
                cur.append(ch)
        i += 1
    if single or double:
        raise Unusual("unbalanced quotes")
    segments.append("".join(cur))
    return [s.strip() for s in segments if s.strip()]


def load_rules() -> tuple[list[str], list[str]]:
    allow: list[str] = []
    deny: list[str] = []
    for path in SETTINGS_FILES:
        try:
            perms = json.loads(path.read_text(encoding="utf-8")).get("permissions", {})
        except (OSError, ValueError):
            continue
        for key, bucket in (("allow", allow), ("deny", deny)):
            for rule in perms.get(key, []):
                if isinstance(rule, str) and rule.startswith("Bash(") and rule.endswith(")"):
                    bucket.append(rule[5:-1])
    return allow, deny


def rule_matches(pattern: str, segment: str) -> bool:
    if pattern.endswith(":*"):
        prefix = pattern[:-2]
        return segment == prefix or segment.startswith(prefix + " ")
    if "*" in pattern:
        return fnmatch.fnmatchcase(segment, pattern)
    return segment == pattern


def hook_allows(hook: str, segment: str) -> bool:
    script = HOOKS_DIR / hook
    if not script.is_file():
        return False
    try:
        res = subprocess.run(
            ["bash", str(script)],
            input=json.dumps({"tool_input": {"command": segment}}),
            capture_output=True,
            text=True,
            timeout=HOOK_TIMEOUT_SECONDS,
        )
        decision = json.loads(res.stdout).get("hookSpecificOutput", {}).get("permissionDecision") if res.stdout.strip() else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    return decision == "allow"


def segment_refusal(segment: str, allow: list[str], deny: list[str]) -> str | None:
    """Return why this segment is not auto-allowed on its own, or None when it is."""
    if GROUPING_START.match(segment):
        return "grouping or subshell"
    for part in segment.split("|"):
        part = part.strip()
        if SPECIAL_START.match(part) or GIT_PUSH.match(part):
            return "make/docker/git push must run alone"
    if any(rule_matches(p, segment) for p in deny):
        return "matches a deny rule"
    if PLAIN_CD.match(segment):
        return None
    if any(rule_matches(p, segment) for p in allow):
        return None
    if hook_allows("allow-readonly-bash.sh", segment) or hook_allows("allow-git-ops.sh", segment):
        return None
    return "not auto-allowed on its own"


def decide(cmd: str) -> str:
    if not cmd.strip():
        return "PASS"
    if len(cmd) > MAX_LENGTH or "\x00" in cmd:
        return "DENY: command too long or binary"
    try:
        segments = split_top_level(cmd)
    except Unusual as exc:
        return f"DENY: {exc}"
    if len(segments) <= 1:
        return "PASS"  # not a compound: the operator characters sat inside quotes
    if len(segments) > MAX_SEGMENTS:
        return f"DENY: more than {MAX_SEGMENTS} segments"
    allow, deny = load_rules()
    for segment in segments:
        why = segment_refusal(segment, allow, deny)
        if why:
            return f"DENY: `{segment[:80]}` ({why})"
    return "PASS"


def main() -> int:
    try:
        verdict = decide(sys.stdin.read())
    except Exception as exc:  # fail closed on any bug
        verdict = f"DENY: internal error ({type(exc).__name__})"
    print(verdict.replace("\n", " "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
