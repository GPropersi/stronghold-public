#!/usr/bin/env python3
"""Shared types and readers for the workflow lint (stdlib only; imported by wf_lint*.py).

Finding: one lint result (severity, id, location, message, repo).
Context: the stronghold root, HOME, the parsed seam-keys.json, the repo scope and the staged flag.
Seam:    python reader for a repo CLAUDE.md `## Claude Config` section. It mirrors lib/seam.sh
         (`seam_section`, `seam_first_token`, `seam_keys`): fenced blocks are ignored, only
         column-0 `- **Key:**` bullets are keys, the section ends at the next `## ` heading, a
         trailing `<!-- -->` comment is dropped, the first backtick token wins, `n/a` reads as unset.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

GIT_ENV_LEAKS = {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX"}
SEVERITY_ORDER = {"error": 0, "warn": 1, "info": 2}
KEY_RE = re.compile(r"^- \*\*([^*]+):\*\*(.*)$")
COMMENT_RE = re.compile(r"\s*<!--.*-->\s*$")
BACKTICK_RE = re.compile(r"^[^`]*`([^`]*)`.*$")

DEFAULT_CONFIG: dict = {
    "required": [],
    "optional": [],
    "legacy": {},
    "worktree": [],
    "exempt": [],
    "unregistered": [],
    "guard": [],
    "pending_deny": [],
    "severity_overrides": {},
    "skill_placeholders": [],
    "relative_path_skills": [],
    "both_forms": [],
    "python_invoked": [],
    "excluded_exempt": [],
    "access_gate_copies": [],
    "fixed_buckets": {"stronghold": "stronghold"},
    "allowlist_exempt": [],
    "template": "",
}


@dataclass(frozen=True)
class Finding:
    severity: str
    id: str
    location: str
    message: str
    repo: str | None = None

    def row(self) -> tuple[str, str, str, str]:
        return (self.severity, self.id, self.location, self.message)

    def sort_key(self) -> tuple:
        return (SEVERITY_ORDER.get(self.severity, 9), self.id, self.location, self.message)


def load_config(root: Path) -> dict:
    """seam-keys.json merged over the defaults (a missing or unreadable file gives the defaults)."""
    config = {key: (value.copy() if hasattr(value, "copy") else value) for key, value in DEFAULT_CONFIG.items()}
    try:
        loaded = json.loads((root / ".claude" / "seam-keys.json").read_text())
    except (OSError, ValueError):
        return config
    if isinstance(loaded, dict):
        for key, value in loaded.items():
            default = DEFAULT_CONFIG.get(key)
            if default is None or isinstance(value, type(default)):
                config[key] = value  # a wrongly typed known key keeps its default
    return config


def git(cwd: Path, *args: str) -> tuple[int, str]:
    """Run git with an argument array; (returncode, stdout). A missing git or cwd reads as rc 127."""
    env = {k: v for k, v in os.environ.items() if k not in GIT_ENV_LEAKS}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        result = subprocess.run(
            ["git", "-c", "core.quotepath=off", *args],
            cwd=cwd, text=True, capture_output=True, check=False, env=env,
        )
    except OSError:
        return 127, ""
    return result.returncode, result.stdout


def expand_home(token: str, home: Path) -> str:
    for prefix in ("~", "$HOME", "${HOME}"):
        if token == prefix:
            return str(home)
        if token.startswith(prefix + "/"):
            return str(home) + token[len(prefix) :]
    return token


class Seam:
    """One repo CLAUDE.md, read the way lib/seam.sh reads it."""

    def __init__(self, md: Path) -> None:
        self.path = md
        try:
            text = md.read_text()
        except (OSError, UnicodeDecodeError):
            text = ""
        self.all_lines = list(enumerate(text.splitlines(), start=1))
        unfenced: list[tuple[int, str]] = []
        fence = False
        for number, line in self.all_lines:
            if line.lstrip().startswith("```"):
                fence = not fence
                continue
            if not fence:
                unfenced.append((number, line))
        self.section: list[tuple[int, str]] = []
        inside = False
        for number, line in unfenced:
            if not inside:
                inside = line == "## Claude Config"
                if inside:
                    self.section.append((number, line))
                continue
            if line.startswith("## "):
                break
            self.section.append((number, line))
        self.has_section = bool(self.section)
        self.entries: list[tuple[str, str, int]] = []  # (key, raw value after the prefix, line)
        for number, line in self.section:
            found = KEY_RE.match(line)
            if found:
                self.entries.append((found.group(1), found.group(2), number))

    def keys(self) -> list[str]:
        return [key for key, _, _ in self.entries]

    def has(self, key: str) -> bool:
        return key in self.keys()

    def line(self, key: str) -> int:
        return next((n for k, _, n in self.entries if k == key), 0)

    def value(self, key: str) -> str | None:
        """The comment-stripped, trimmed text after `- **Key:**`; None when the key is absent."""
        for entry_key, raw, _ in self.entries:
            if entry_key == key:
                return COMMENT_RE.sub("", raw).strip()
        return None

    def token(self, key: str) -> str:
        """Mirror of seam_first_token: first backtick token, else first word; '' for n/a/absent."""
        value = self.value(key)
        if not value:
            return ""
        if value.count("`") >= 2:
            found = BACKTICK_RE.match(value)
            token = found.group(1) if found else ""
        else:
            words = value.split()
            token = words[0] if words else ""
        return "" if token == "n/a" else token

    def block(self, key: str) -> list[tuple[int, str]]:
        """Lines after the key's bullet up to the next column-0 bullet (its table/prose)."""
        start = next((i for i, (n, _) in enumerate(self.section) if n == self.line(key)), None)
        if start is None:
            return []
        out: list[tuple[int, str]] = []
        for number, line in self.section[start + 1 :]:
            if KEY_RE.match(line):
                break
            out.append((number, line))
        return out


class Context:
    def __init__(
        self,
        root: Path,
        home: Path,
        cfg: dict,
        repo: str | None = None,
        staged: bool = False,
    ) -> None:
        self.root = root
        self.home = home
        self.cfg = cfg
        self.repo_filter = repo
        self.staged = staged
        self._seams: dict[str, Seam] = {}
        self._repos: list[str] | None = None

    @property
    def repos(self) -> list[str]:
        """Immediate child directories holding a `.git` DIRECTORY (linked worktrees excluded)."""
        if self._repos is None:
            try:
                names = sorted(
                    p.name
                    for p in self.root.iterdir()
                    if p.is_dir() and not p.is_symlink() and not p.name.startswith(".") and (p / ".git").is_dir()
                )
            except OSError:
                names = []
            self._repos = names
        return self._repos

    def scoped_repos(self) -> list[str]:
        return [r for r in self.repos if self.repo_filter in (None, r)]

    def is_exempt(self, repo: str) -> bool:
        return repo in self.cfg["exempt"]

    def seam(self, repo: str) -> Seam:
        if repo not in self._seams:
            self._seams[repo] = Seam(self.root / repo / "CLAUDE.md")
        return self._seams[repo]

    def is_bot_repo(self, repo: str) -> bool:
        """On the bot-push path: not exempt and `Bot push script` names a real script."""
        return not self.is_exempt(repo) and bool(self.seam(repo).token("Bot push script"))

    def bucket_of(self, repo: str) -> str:
        return self.seam(repo).token("Plans bucket")

    def declared_buckets(self) -> dict[str, list[str]]:
        """bucket -> repos declaring it (exempt repos and absent keys left out)."""
        out: dict[str, list[str]] = {}
        for repo in self.repos:
            bucket = "" if self.is_exempt(repo) else self.bucket_of(repo)
            if bucket:
                out.setdefault(bucket, []).append(repo)
        return out

    def repo_for_bucket(self, bucket: str) -> str | None:
        """The repo directory owning `bucket`: its declarer, else a directory of the same name."""
        declared = self.declared_buckets().get(bucket)
        if declared:
            return declared[0]
        return bucket if bucket in self.repos else None

    def rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)

