"""Hygiene group of the workflow lint: tracked PNGs, token patterns, changelog, staging scope, imports."""

from __future__ import annotations

import ast
import fnmatch
import re
from pathlib import Path

import wf_lint_base as base

MOCK_PNG_RE = re.compile(r"^plans/.*/(mocks|mockup|mockups)/.*\.png$", re.IGNORECASE)
SCREENSHOT_RE = re.compile(r"^plans/.*/screenshots/.+")
SECRET_PATTERNS = (
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}")),
    ("github-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}")),
    # real node ids mix in lowercase letters or digits; ALL_CAPS placeholders (BOT_NODE_ID) are not ids
    ("graphql-node-id", re.compile(r"\b(?:PVT_|PVTSSF_|PVTSSO_|BOT_|PR_kw)(?=[A-Za-z0-9_-]*[a-z0-9])[A-Za-z0-9_-]{6,}")),
    ("hex-64", re.compile(r"(?<![0-9A-Za-z])[0-9a-f]{64}(?![0-9A-Za-z])")),
    ("private-key-header", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)
FILENAME_RE = re.compile(r"^(\d{2})-(\d{2})-(\d{4})-changelog\.md$")
ENTRY_RE = re.compile(r"^- \[[0-2][0-9]:[0-5][0-9]\] [^:]+: .+")
STAMP_RE = re.compile(r"^- \[([0-9]{2}):([0-9]{2})\]")
MAX_SCAN_BYTES = 1_000_000


def _tracked(ctx: base.Context) -> list[str]:
    code, out = base.git(ctx.root, "ls-files")
    return sorted(out.splitlines()) if code == 0 else []


def _staged_paths(ctx: base.Context) -> list[str]:
    code, out = base.git(ctx.root, "diff", "--cached", "--name-only", "--diff-filter=ACMR")
    return sorted(out.splitlines()) if code == 0 else []


def _scoped_paths(ctx: base.Context) -> list[str]:
    return _staged_paths(ctx) if ctx.staged else _tracked(ctx)


def _png_checks(ctx: base.Context) -> list[base.Finding]:
    out = []
    for path in _scoped_paths(ctx):
        if MOCK_PNG_RE.match(path) or SCREENSHOT_RE.match(path):
            out.append(base.Finding("error", "git.no-tracked-mock-pngs", path,
                                    "mock PNGs and screenshots are regenerable derivatives: never track them"))
    return out


def _load_allowlist(ctx: base.Context) -> tuple[set[str], list[str]]:
    values: set[str] = set()
    globs: list[str] = []
    try:
        text = (ctx.root / ".claude" / "secret-scan-allow.txt").read_text()
    except OSError:
        return values, globs
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("path:"):
            globs.append(line[len("path:") :].strip())
        else:
            values.add(line)
    return values, globs


def _scan_line(line: str, allowed: set[str]) -> list[str]:
    names = []
    for name, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(line):
            if match.group(0) not in allowed:
                names.append(name)
                break
    return names


def _secret_checks(ctx: base.Context) -> list[base.Finding]:
    allowed, globs = _load_allowlist(ctx)
    out: list[base.Finding] = []

    def skip(path: str) -> bool:
        return any(fnmatch.fnmatch(path, g) for g in globs)

    def flag(path: str, number: int, name: str) -> None:
        out.append(base.Finding("error", "secrets.no-token-patterns", f"{path}:{number}",
                                f"matches the {name} pattern (value not shown)"))

    if ctx.staged:
        code, diff = base.git(
            ctx.root, "diff", "--cached", "-U0", "--no-color", "--no-ext-diff", "--no-textconv",
            "--src-prefix=a/", "--dst-prefix=b/", "--diff-filter=ACMR",
        )
        path, number = "", 0
        old_left = new_left = 0  # hunk line budget: any "+" line inside a hunk is content
        for line in diff.split("\n") if code == 0 else []:
            if old_left or new_left:
                if line.startswith("+"):
                    new_left -= 1
                    number += 1
                    if path and not skip(path):
                        for name in _scan_line(line[1:], allowed):
                            flag(path, number, name)
                elif line.startswith("-"):
                    old_left -= 1
                continue
            if line.startswith("diff --git"):
                path = ""
            elif line.startswith("+++ "):
                path = line[6:].split("\t")[0] if line.startswith("+++ b/") else ""
            elif line.startswith("@@"):
                found = re.match(r"@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
                if found:
                    old_left = int(found.group(1) or 1)
                    new_left = int(found.group(3) or 1)
                    number = int(found.group(2)) - 1
                    if not path:
                        out.append(base.Finding("error", "secrets.no-token-patterns", "(staged diff)",
                                                "cannot determine the path of a staged hunk, so it was not scanned"))
        return out
    for path in _tracked(ctx):
        file = ctx.root / path
        if skip(path) or file.is_symlink() or not file.is_file():
            continue
        try:
            if file.stat().st_size > MAX_SCAN_BYTES:
                out.append(base.Finding("info", "secrets.no-token-patterns", path, "file too large to scan"))
                continue
            text = file.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            continue
        for number, line in enumerate(text.split("\n"), start=1):
            for name in _scan_line(line, allowed):
                flag(path, number, name)
    return out


def _changelog_checks(ctx: base.Context) -> list[base.Finding]:
    out: list[base.Finding] = []
    directory = ctx.root / "changelog"
    if not directory.is_dir():
        return out
    staged = set(_staged_paths(ctx)) if ctx.staged else None
    for file in sorted(p for p in directory.iterdir() if p.is_file()):
        rel = ctx.rel(file)
        if staged is not None and rel not in staged:
            continue
        match = FILENAME_RE.match(file.name)
        if not match:
            out.append(base.Finding("error", "changelog.filename-format", rel, "name must be MM-DD-YYYY-changelog.md"))
            continue
        try:
            lines = file.read_text().splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        month, day, year = match.groups()
        expected = f"# Changelog — {month}/{day}/{year}"
        header = next((line for line in lines if line.strip()), "")
        if header != expected:
            out.append(base.Finding("error", "changelog.header-matches-filename", f"{rel}:1", f"first line must be `{expected}`"))
        previous = ""
        regressions: list[tuple[int, str, str]] = []
        for number, line in enumerate(lines, start=1):
            if not line.startswith("- ["):
                continue
            if not ENTRY_RE.match(line):
                out.append(base.Finding("error", "changelog.entry-format", f"{rel}:{number}",
                                        "entry must be `- [HH:MM] <action>: <summary>`"))
                continue
            stamp = STAMP_RE.match(line)
            current = f"{stamp.group(1)}:{stamp.group(2)}" if stamp else ""
            if previous and current < previous:
                regressions.append((number, current, previous))
            previous = current
        if regressions:
            number, current, earlier = regressions[0]
            more = f" (+{len(regressions) - 1} more in this file)" if len(regressions) > 1 else ""
            out.append(base.Finding("warn", "changelog.timestamps-nondecreasing", f"{rel}:{number}",
                                    f"[{current}] is earlier than the previous entry [{earlier}]{more}"))
    return out


def _staging_scope(ctx: base.Context) -> list[base.Finding]:
    location = ".claude/scripts/stronghold-sync.sh"
    try:
        script = (ctx.root / location).read_text()
        doc = (ctx.root / "CLAUDE.md").read_text()
    except (OSError, UnicodeDecodeError):
        return [base.Finding("error", "git.staging-scope-consistent", location,
                             "stronghold-sync.sh or CLAUDE.md cannot be read")]
    in_script = re.search(r"^for p in ([^;]+); do", script, re.MULTILINE)
    in_doc = re.search(r"path-scoped staging only \(`([^`]+)`", doc)
    if not in_script or not in_doc:
        return [base.Finding("error", "git.staging-scope-consistent", location,
                             "cannot find the staged-path list in the script or in CLAUDE.md")]

    def norm(text: str) -> set[str]:
        return {item.rstrip("/") for item in text.split()}

    script_paths, doc_paths = norm(in_script.group(1)), norm(in_doc.group(1))
    if script_paths == doc_paths:
        return []
    only_script = ", ".join(sorted(script_paths - doc_paths)) or "-"
    only_doc = ", ".join(sorted(doc_paths - script_paths)) or "-"
    return [base.Finding("error", "git.staging-scope-consistent", location,
                         f"script stages [{only_script}] that CLAUDE.md omits, CLAUDE.md lists [{only_doc}] that the script omits")]


def _local_imports(tree: ast.AST) -> list[int]:
    lines: list[int] = []

    def visit(node: ast.AST, nested: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.Import, ast.ImportFrom)) and nested:
                lines.append(child.lineno)
            visit(child, nested or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))

    visit(tree, False)
    return lines


def _import_checks(ctx: base.Context) -> list[base.Finding]:
    out: list[base.Finding] = []
    scripts = ctx.root / ".claude" / "scripts"
    if not scripts.is_dir():
        return out
    for file in sorted(scripts.rglob("*.py")):
        if "__pycache__" in file.parts:
            continue
        rel = ctx.rel(file)
        try:
            tree = ast.parse(file.read_text())
        except (SyntaxError, ValueError, OSError, UnicodeDecodeError) as error:
            out.append(base.Finding("error", "py.no-local-imports", rel, f"cannot parse: {error}"))
            continue
        for line in _local_imports(tree):
            out.append(base.Finding("error", "py.no-local-imports", f"{rel}:{line}",
                                    "import inside a function or class (top-level imports only)"))
    return out


def run(ctx: base.Context) -> list[base.Finding]:
    findings: list[base.Finding] = []
    for check in (_png_checks, _secret_checks, _changelog_checks, _staging_scope, _import_checks):
        findings.extend(check(ctx))
    return findings
