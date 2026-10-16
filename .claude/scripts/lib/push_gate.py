#!/usr/bin/env python3
"""Core of push-gate.sh: staleness, path-to-suite scope mapping, sequential suite runs and the
HEAD-sha-keyed result file (stdlib only). Every verdict comes from git state, files and exit codes.

    push_gate.py run <full|precheck> <branch>   preconditions, fetch, staleness, scope [, suites]
    push_gate.py status <branch>                stored result path + JSON for the branch tip, or `none`
    push_gate.py check [-C <dir>] <branch>      exit 0 for a pass/na result keyed to the tip sha,
                                                else exit 1 with a one-line reason

A row's `a && b` command is split into the independent suites `a` and `b`, and each distinct
suite runs once even when several matched rows contain it (see split_command for what stays whole).

Result file: <git-common-dir>/push-gate/<sha>.json (schema 1). Exit codes of `run`: 0 pass or na,
1 a suite failed, 3 precondition (stale, dirty tracked files in full mode, detached or wrong HEAD,
default branch, unmatched path, empty changed set, Worktree policy denial), 4 could not run (no
seam key, malformed rows, fetch failed, gh unavailable for a multi-commit branch, missing suite).
Env: PG_GH (gh binary, default gh-bot.sh), PG_LOG_DIR (default /tmp/claude).
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB_DIR))

import seam_push_gate  # noqa: E402

SCHEMA = 1


class Stop(Exception):
    def __init__(self, code: int, message: str, extra: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra or {}


def git(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True)


def git_out(*args: str, cwd: str | None = None) -> str:
    result = git(*args, cwd=cwd)
    return result.stdout.strip() if result.returncode == 0 else ""


def common_dir(cwd: str | None = None) -> str:
    out = git_out("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=cwd)
    return os.path.realpath(out) if out else ""


def repo_name(common: str) -> str:
    base = os.path.dirname(common) if os.path.basename(common) == ".git" else common
    return os.path.basename(base)


def default_branch(md_path: Path) -> str:
    try:
        text = md_path.read_text()
    except (OSError, UnicodeDecodeError):
        return "main"
    for _, line in seam_push_gate._section_lines(text):
        match = re.match(r"- \*\*Default branch:\*\*\s*`?([^`\s]+)", line)
        if match and match.group(1).lower() != "n/a":
            return match.group(1)
    return "main"


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def remove_old_results(gate_dir: Path, branch: str, keep: str) -> None:
    if not gate_dir.is_dir():
        return
    for item in gate_dir.glob("*.json.tmp*"):
        item.unlink(missing_ok=True)
    for item in gate_dir.glob("*.json"):
        if item.name == f"{keep}.json":
            continue
        try:
            old = json.loads(item.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(old, dict) and old.get("branch") == branch:
            item.unlink(missing_ok=True)


def _result_branch(path: Path) -> str | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data.get("branch") if isinstance(data, dict) else None


def subject(sha: str, cwd: str) -> str:
    return git_out("log", "-1", "--format=%s", sha, cwd=cwd)


def lookup_merged_pr(branch: str, base: str, cwd: str) -> tuple[dict | None, list[dict], bool]:
    """(merged_pr, commits, available). Asks gh for merged PRs of the branch; a merged
    headRefOid that is an ancestor of HEAD and ahead of the base means those commits are merged."""
    gh = os.environ.get("PG_GH") or str(LIB_DIR.parent / "gh-bot.sh")
    try:
        result = subprocess.run(
            [gh, "pr", "list", "--head", branch, "--state", "merged",
             "--json", "number,headRefOid", "--limit", "30"],
            cwd=cwd, text=True, capture_output=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, [], False
    if result.returncode != 0:
        return None, [], False
    try:
        prs = json.loads(result.stdout) if result.stdout.strip() else []
    except ValueError:
        return None, [], False
    if not isinstance(prs, list):
        return None, [], False
    for pr in prs:
        oid = pr.get("headRefOid") if isinstance(pr, dict) else None
        if not oid or git("merge-base", "--is-ancestor", oid, "HEAD", cwd=cwd).returncode != 0:
            continue
        listing = git_out("log", "--reverse", "--format=%H%x00%s", f"{base}..{oid}", cwd=cwd)
        commits = [
            {"sha": line.split("\0")[0], "subject": line.split("\0")[1]}
            for line in listing.splitlines() if "\0" in line
        ]
        if commits:
            return {"number": pr.get("number"), "head_oid": oid}, commits, True
    return None, [], True


def staleness(branch: str, base: str, cwd: str) -> dict:
    cherry = git_out("cherry", base, "HEAD", cwd=cwd)
    minus, plus = [], []
    for line in cherry.splitlines():
        mark, _, sha = line.partition(" ")
        (minus if mark == "-" else plus).append(sha)
    stale = {"status": "CLEAN", "commits": [], "merged_pr": None}
    if minus:
        stale["status"] = "STALE"
        stale["commits"] = [{"sha": sha, "subject": subject(sha, cwd)} for sha in minus]
        return stale
    merged, commits, available = lookup_merged_pr(branch, base, cwd)
    if not available:
        if len(plus) > 1:
            raise Stop(4, "cannot verify merged-PR state (gh unavailable) and git cherry cannot "
                          "exclude a squash merge of a multi-commit branch")
        print("push-gate.sh: warning: gh unavailable, staleness checked with git cherry only",
              file=sys.stderr)
    elif merged is not None:
        stale["status"] = "STALE"
        stale["commits"] = commits
        stale["merged_pr"] = merged
    return stale


STATEFUL_WORDS = {
    "cd", "pushd", "popd", "export", "unset", "set", "source", ".", "alias", "unalias", "umask",
    "ulimit", "eval", "exec", "shopt", "trap", "read", "local", "declare", "typeset", "readonly",
    "if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done", "case", "esac",
    "select", "function", "{", "}",
}
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def split_command(command: str) -> list[str]:
    """The `&&`-chained pieces of a suite command, so a piece shared by two rows runs once.

    Returns [command] unchanged (run whole, as before) unless every piece is independent: any
    other shell operator (`;` `|` `||` a lone `&`, parentheses, backticks, `$(`, a newline, a
    comment), an unterminated quote, an empty piece, or a piece that carries shell state to the
    next one (`cd sub`, `export X=1`, a bare `X=1`, a compound-statement keyword) keeps it whole.
    """
    pieces: list[str] = []
    current: list[str] = []
    quote = ""
    word_start = True
    i = 0
    while i < len(command):
        char = command[i]
        if quote:
            current.append(char)
            if char == "\\" and quote == '"' and i + 1 < len(command):
                i += 1
                current.append(command[i])
            elif char == quote:
                quote = ""
            word_start = False
            i += 1
            continue
        if char == "\\" and i + 1 < len(command):
            current.append(char + command[i + 1])
            word_start = False
            i += 2
            continue
        if char in "'\"":
            quote = char
        elif char in ";|()`\n" or (char == "$" and command[i + 1:i + 2] == "("):
            return [command]
        elif char == "#" and word_start:
            return [command]
        elif char == "&":
            if command[i + 1:i + 2] != "&":
                return [command]
            pieces.append("".join(current).strip())
            current = []
            word_start = True
            i += 2
            continue
        current.append(char)
        word_start = char.isspace()
        i += 1
    if quote:
        return [command]
    pieces.append("".join(current).strip())
    if len(pieces) < 2 or not all(pieces):
        return [command]
    for piece in pieces:
        try:
            words = shlex.split(piece)
        except ValueError:
            return [command]
        while words and ASSIGNMENT.match(words[0]):
            words.pop(0)
        if not words or words[0] in STATEFUL_WORDS:
            return [command]
    return pieces


def map_scope(changed: list[str], rows: list[dict]) -> dict:
    suites: dict[str, list[str]] = {}
    row_pos: dict[str, tuple[int, int]] = {}
    na_paths: list[str] = []
    unmatched: list[str] = []
    for path in changed:
        index = next(
            (i for i, r in enumerate(rows) if any(seam_push_gate.glob_match(g, path) for g in r["globs"])),
            None,
        )
        row = rows[index] if index is not None else None
        if row is None:
            unmatched.append(path)
        elif row["command"] is None:
            na_paths.append(path)
        else:
            for position, piece in enumerate(dict.fromkeys(split_command(row["command"]))):
                suites.setdefault(piece, []).append(path)
                row_pos[piece] = min(row_pos.get(piece, (index, position)), (index, position))
    # run order follows the earliest matching row (then the position inside it) of each suite
    order = sorted(suites, key=lambda cmd: row_pos[cmd])
    return {
        "changed_paths": changed,
        "suites": [{"command": cmd, "matched_paths": suites[cmd]} for cmd in order],
        "na_paths": na_paths,
        "unmatched": unmatched,
    }


def policy_reason(command: str, toplevel: str) -> str:
    result = subprocess.run(
        ["bash", str(LIB_DIR / "pg-policy.sh"), command, toplevel],
        text=True, capture_output=True,
    )
    if result.returncode != 0:
        raise Stop(4, f"Worktree policy check failed (exit {result.returncode}): {result.stderr.strip()}")
    return result.stdout.strip()


def summary_line(log_path: str) -> str:
    try:
        lines = [ln.strip() for ln in Path(log_path).read_text(errors="replace").splitlines() if ln.strip()]
    except OSError:
        return ""
    return lines[-1] if lines else ""


def run_suites(scope_suites: list[dict], toplevel: str, repo: str, sha: str) -> list[dict]:
    log_dir = Path(os.environ.get("PG_LOG_DIR") or "/tmp/claude")
    log_dir.mkdir(parents=True, exist_ok=True)
    done: list[dict] = []
    for index, suite in enumerate(scope_suites, start=1):
        log_path = str(log_dir / f"gate-{repo}-{sha[:7]}-{index}.txt")
        with open(log_path, "wb") as log:
            try:
                proc = subprocess.run(
                    ["bash", "-c", suite["command"]], cwd=toplevel, stdin=subprocess.DEVNULL,
                    stdout=log, stderr=subprocess.STDOUT,
                )
                code = proc.returncode
            except OSError as err:
                raise Stop(4, f"could not run suite {suite['command']!r}: {err}")
        if code in (126, 127):
            raise Stop(4, f"suite command not found or not executable (exit {code}): {suite['command']!r}")
        done.append({"command": suite["command"], "exit": code,
                     "summary_line": summary_line(log_path), "log_path": log_path})
        if code != 0:
            break
    return done


def run(mode: str, branch: str) -> int:
    report: dict = {"mode": mode, "branch": branch}
    try:
        return _run(mode, branch, report)
    except Stop as stop:
        report.update(stop.extra)
        report.update({"status": "refused", "reason": stop.message})
        print(f"push-gate.sh: {stop.message}", file=sys.stderr)
        print(json.dumps(report))
        return stop.code


def _run(mode: str, branch: str, report: dict) -> int:
    toplevel = git_out("rev-parse", "--show-toplevel")
    if not toplevel:
        raise Stop(4, "not inside a git checkout")
    common = common_dir(toplevel)
    repo = repo_name(common)
    head_ref = git_out("symbolic-ref", "-q", "HEAD", cwd=toplevel)
    if head_ref != f"refs/heads/{branch}":
        why = "detached HEAD" if not head_ref else f"HEAD is {head_ref}"
        raise Stop(3, f"HEAD is not refs/heads/{branch} ({why})")
    md_path = Path(toplevel) / "CLAUDE.md"
    default = default_branch(md_path)
    if branch == default:
        raise Stop(3, f"{branch} is the default branch: nothing to gate")
    if mode == "full" and git_out("status", "--porcelain", "--untracked-files=no", cwd=toplevel):
        raise Stop(3, "dirty tracked files: commit or stash before the full gate")

    parsed = seam_push_gate.parse(md_path)
    if not parsed["present"]:
        raise Stop(4, f"declare a Push gate key in {repo}/CLAUDE.md (no fallback suite is guessed)")
    if parsed["errors"]:
        first = parsed["errors"][0]
        raise Stop(4, f"malformed Push gate row in {repo}/CLAUDE.md:{first['line']}: {first['message']}")

    fetch = git("fetch", "-q", "origin", cwd=toplevel)
    if fetch.returncode != 0:
        raise Stop(4, f"git fetch origin failed (the gate never certifies on stale refs): {fetch.stderr.strip()}")
    base = f"origin/{default}"
    base_sha = git_out("rev-parse", base, cwd=toplevel)
    if not base_sha:
        raise Stop(4, f"{base} does not exist after fetch")

    diff = git("diff", "--name-only", "--no-renames", "--no-ext-diff", "-z", f"{base}...HEAD", cwd=toplevel)
    if diff.returncode != 0:
        raise Stop(4, f"git diff {base}...HEAD failed: {diff.stderr.strip()}")
    raw = diff.stdout
    changed = sorted(p for p in raw.split("\0") if p)
    if not changed:
        raise Stop(3, f"nothing to push (no commits ahead of {base})")

    sha = git_out("rev-parse", "HEAD", cwd=toplevel)
    stale = staleness(branch, base, toplevel)
    scope = map_scope(changed, parsed["rows"])
    report.update({"sha": sha, "repo": repo, "base": base, "base_sha": base_sha,
                   "stale": stale, "scope": scope})
    if stale["status"] == "STALE":
        raise Stop(3, f"branch is STALE: {len(stale['commits'])} commit(s) already merged into {base} "
                      "(rebase onto the default branch)")
    if scope["unmatched"]:
        raise Stop(3, "changed paths match no Push gate row (add a row or an explicit `na` row): "
                      + ", ".join(scope["unmatched"]))
    scope.pop("unmatched")

    if mode == "precheck":
        report.update({"status": "precheck", "checked_at": now_iso()})
        print(json.dumps(report))
        return 0

    for suite in scope["suites"]:
        reason = policy_reason(suite["command"], toplevel)
        if reason:
            raise Stop(3, reason)

    gate_dir = Path(common) / "push-gate"
    result_path = gate_dir / f"{sha}.json"
    result = {
        "schema": SCHEMA, "mode": "full", "sha": sha, "branch": branch, "repo": repo,
        "base": base, "base_sha": base_sha, "checked_at": now_iso(),
        "stale": {"status": "CLEAN", "commits": [], "merged_pr": None},
        "scope": scope, "suites": [], "status": "running",
    }
    remove_old_results(gate_dir, branch, sha)
    if not scope["suites"]:
        result["status"] = "na"
        write_atomic(result_path, result)
        print(json.dumps(result))
        return 0
    write_atomic(result_path, result)
    try:
        result["suites"] = run_suites(scope["suites"], toplevel, repo, sha)
    except Stop:
        raise
    result["status"] = "pass" if all(s["exit"] == 0 for s in result["suites"]) else "fail"
    write_atomic(result_path, result)
    print(json.dumps(result))
    return 0 if result["status"] == "pass" else 1


def tip_and_path(branch: str, cwd: str | None) -> tuple[str, Path | None]:
    sha = git_out("rev-parse", "--verify", "-q", f"refs/heads/{branch}", cwd=cwd)
    common = common_dir(cwd)
    if not sha or not common:
        return "", None
    return sha, Path(common) / "push-gate" / f"{sha}.json"


def status(branch: str) -> int:
    sha, path = tip_and_path(branch, None)
    if path is None or not path.is_file():
        print("none")
        return 0
    print(path)
    print(path.read_text())
    return 0


def check_reason(branch: str, cwd: str | None) -> str | None:
    """None when the stored result passes, else a one-line reason."""
    sha, path = tip_and_path(branch, cwd)
    if not sha:
        return f"no branch refs/heads/{branch} in this repo"
    if path is None or not path.is_file():
        stale = ""
        if path is not None and any(_result_branch(other) == branch for other in path.parent.glob("*.json")):
            stale = " (stale gate result: the branch moved after the gate ran)"
        return f"no result for {branch} at {sha[:7]}{stale}: run push-gate.sh {branch}"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as err:
        return f"result file for {sha[:7]} does not parse: {err}"
    if not isinstance(data, dict):
        return f"result file for {sha[:7]} does not parse: not a JSON object"
    if data.get("schema") != SCHEMA:
        return f"result for {sha[:7]} has schema {data.get('schema')!r} (expected {SCHEMA})"
    if data.get("sha") != sha or path.stem != sha:
        return f"result sha field {data.get('sha')!r} does not match the file/tip sha {sha[:7]}"
    if data.get("mode") != "full":
        return f"result for {sha[:7]} has mode {data.get('mode')!r} (expected full)"
    state = data.get("status")
    if state == "running":
        return f"gate still running or was killed for {sha[:7]}: rerun push-gate.sh {branch}"
    if state not in ("pass", "na"):
        return f"gate status is {state!r} for {sha[:7]}: fix and rerun push-gate.sh {branch}"
    suites = data.get("suites")
    if not isinstance(suites, list) or any(
        not isinstance(s, dict) or type(s.get("exit")) is not int or s["exit"] != 0 for s in suites
    ):
        return f"result for {sha[:7]} is {state} but a suite exit is not 0"
    if (state == "pass") != bool(suites):
        return f"result for {sha[:7]} is {state} with {len(suites)} suite(s): inconsistent"
    return None


def check(argv: list[str]) -> int:
    cwd = None
    if argv[:1] == ["-C"] and len(argv) >= 2:
        cwd, argv = argv[1], argv[2:]
    if len(argv) != 1:
        print(__doc__)
        return 2
    reason = check_reason(argv[0], cwd)
    if reason is None:
        print(f"push-gate result ok for {argv[0]}")
        return 0
    print(reason)
    return 1


def main(argv: list[str]) -> int:
    try:
        return _main(argv)
    except Stop as stop:
        print(f"push-gate.sh: {stop.message}", file=sys.stderr)
        return stop.code
    except Exception as err:  # noqa: BLE001 - an unhandled error must never read as "a suite failed"
        if len(argv) >= 2 and argv[1] == "check":
            print(f"check failed: {err}")
            return 1
        print(f"push-gate.sh: internal error: {err}", file=sys.stderr)
        return 4


def _main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "run" and len(argv) == 4 and argv[2] in ("full", "precheck"):
        return run(argv[2], argv[3])
    if len(argv) == 3 and argv[1] == "status":
        return status(argv[2])
    if len(argv) >= 2 and argv[1] == "check":
        return check(argv[2:])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
