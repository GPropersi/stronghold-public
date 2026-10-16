"""Singleton lock helper for `singleton-runtime` repos (driven by wt-singleton.sh). Stdlib only.

One lock per repo, keyed by the repo slug basename, under --lock-dir:
  <lock_dir>/<repo>.lock          flock target (held only across a short read-check-write section)
  <lock_dir>/<repo>.holder.json   persistent holder record, replaced atomically

A PreToolUse hook cannot hold a lock and `make up` for a compose bot returns while the bot keeps
running, so the lock is a persistent holder record with a liveness check, not just an flock.

Holder record: {"repo", "worktree", "branch", "pid", "target", "started": "YYYY-MM-DDTHH:MM:SS"}.
`pid` is the CALLER's pid (the wrapper's own, `--pid $$`), never this helper's.

Liveness (see is_live): a CORRUPT record is live; unless --docker-only the recorded pid being alive
is live; otherwise a running compose container whose
`com.docker.compose.project.working_dir` label is the holder's checkout, or a path under it
(excluding <checkout>/.claude/worktrees/), is live. docker missing, failing or timing out reads
as live (fail closed). A container with an empty label is skipped.

CLI (see `main`):
  wt_singleton.py acquire --lock-dir D --repo R --worktree W --branch B --pid P --target T
                          [--lock-timeout S]
  wt_singleton.py release --lock-dir D --repo R [--force] [--expect-pid P] [--lock-timeout S]
  wt_singleton.py status  --lock-dir D --repo R
  wt_singleton.py is-live --lock-dir D --repo R [--docker-only]

Exit codes: 0 ok / live, 1 error, 3 refused (holder printed to stderr), 4 lock timeout,
5 is-live: no record or not live. `status` and `is-live` are lock-free reads of the atomically
replaced record and never take the flock.
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

# Strictly shorter than the default --lock-timeout, because the probe runs inside the flock
# critical section: a hung docker must not make a concurrent waiter time out first.
DOCKER_PROBE_TIMEOUT: int = 5
DEFAULT_LOCK_TIMEOUT: float = 10.0
LOCK_POLL_SECONDS: float = 0.05
WORKING_DIR_LABEL: str = "com.docker.compose.project.working_dir"
DOCKER_PS_FORMAT: str = "{{.ID}}\t{{.Label \"" + WORKING_DIR_LABEL + "\"}}"
WORKTREES_SUBDIR: str = os.path.join(".claude", "worktrees")
APPROVAL_HINT: str = "clearing it needs `release --force`, which needs the user's approval"

EXIT_OK: int = 0
EXIT_ERROR: int = 1
EXIT_REFUSED: int = 3
EXIT_LOCK_TIMEOUT: int = 4
EXIT_NOT_LIVE: int = 5

RECORD_STRING_KEYS: tuple[str, ...] = ("repo", "worktree", "branch", "target", "started")
MAX_PID: int = 2**31 - 1
# The repo slug becomes a file name under --lock-dir: no separators, no leading dot or dash.
REPO_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class _Corrupt:
    """Sentinel type: the holder file exists but cannot be trusted (held-by-unknown)."""

    def __repr__(self) -> str:
        return "CORRUPT"


CORRUPT = _Corrupt()
Record = dict[str, object]


class LockTimeout(Exception):
    """The per-repo flock could not be taken within the bounded wait."""


def holder_path(lock_dir: Path, repo: str) -> Path:
    return lock_dir / f"{repo}.holder.json"


def lock_path(lock_dir: Path, repo: str) -> Path:
    return lock_dir / f"{repo}.lock"


def read_record(path: Path) -> Record | _Corrupt | None:
    """None when the file is absent, the parsed record, or CORRUPT for anything unusable."""
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        return CORRUPT
    try:
        record = json.loads(text)
    except ValueError:
        return CORRUPT
    if not isinstance(record, dict):
        return CORRUPT
    if not all(isinstance(record.get(key), str) for key in RECORD_STRING_KEYS):
        return CORRUPT
    pid = record.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid > MAX_PID:
        return CORRUPT
    return record


def describe(record: Record) -> str:
    return (
        f"repo={record['repo']} worktree={record['worktree']} branch={record['branch']} "
        f"pid={record['pid']} target={record['target']} started={record['started']}"
    )


@contextlib.contextmanager
def locked(lock_dir: Path, repo: str, timeout: float) -> Iterator[None]:
    """Hold the repo's flock for the body, polling LOCK_NB until `timeout` elapses."""
    os.makedirs(lock_dir, mode=0o700, exist_ok=True)
    target = lock_path(lock_dir, repo)
    descriptor = os.open(target, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as busy:
                if busy.errno not in (errno.EAGAIN, errno.EACCES):
                    raise
                if time.monotonic() >= deadline:
                    raise LockTimeout(f"could not take the lock {target} within {timeout:g}s") from busy
                time.sleep(LOCK_POLL_SECONDS)
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _normalise(path: str) -> str:
    return os.path.realpath(path).rstrip(os.sep)


def _docker_working_dirs() -> list[str] | None:
    """Working-dir labels of running containers, or None when docker cannot answer (fail closed)."""
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", DOCKER_PS_FORMAT],
            capture_output=True,
            text=True,
            timeout=DOCKER_PROBE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        print(
            f"wt-singleton: docker probe timed out after {DOCKER_PROBE_TIMEOUT}s, "
            "assuming holder is live",
            file=sys.stderr,
        )
        return None
    except OSError as unavailable:
        print(f"wt-singleton: docker unavailable ({unavailable}), assuming holder is live", file=sys.stderr)
        return None
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit {result.returncode}"
        print(f"wt-singleton: docker ps failed ({detail}), assuming holder is live", file=sys.stderr)
        return None
    labels = []
    # Split on newline only: str.splitlines() also breaks on \x0b, \x0c, \x85 and the like, which a
    # working_dir may contain and which would truncate the label (fail open).
    for line in result.stdout.split("\n"):
        _, _, label = line.partition("\t")
        label = label.rstrip("\r")
        if label.strip():
            labels.append(label)
    return labels


def is_live(record: Record | _Corrupt, docker_only: bool = False) -> bool:
    """Ordered liveness rule; see the module docstring. Fail closed whenever docker is unsure."""
    if isinstance(record, _Corrupt):
        return True
    pid = record["pid"]
    # read_record guarantees an int pid; the isinstance only narrows the type for the checker.
    if not docker_only and isinstance(pid, int) and _pid_alive(pid):
        return True
    labels = _docker_working_dirs()
    if labels is None:
        return True
    checkout = _normalise(str(record["worktree"]))
    nested_worktrees = os.path.join(checkout, WORKTREES_SUBDIR)
    for label in labels:
        path = _normalise(label)
        if path == nested_worktrees or path.startswith(nested_worktrees + os.sep):
            continue
        if path == checkout or path.startswith(checkout + os.sep):
            return True
    return False


def acquire(lock_dir: Path, repo: str, holder: Record, timeout: float) -> Record | _Corrupt | None:
    """None on success (record written); the live record, or CORRUPT, when refused."""
    with locked(lock_dir, repo, timeout):
        path = holder_path(lock_dir, repo)
        existing = read_record(path)
        if existing is not None and is_live(existing):
            return existing
        descriptor, temp_name = tempfile.mkstemp(dir=lock_dir, prefix=f".{repo}.holder.", suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w") as handle:
                json.dump(holder, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp_name)
            raise
        return None


def release(
    lock_dir: Path, repo: str, force: bool, expect_pid: int | None, timeout: float
) -> bool:
    """Drop the record. Refuses (False) a live record unless forced or `expect_pid` matches it."""
    with locked(lock_dir, repo, timeout):
        path = holder_path(lock_dir, repo)
        record = read_record(path)
        if record is None:
            return True
        if not force:
            owned = (
                not isinstance(record, _Corrupt)
                and expect_pid is not None
                and record["pid"] == expect_pid
            )
            if not owned and is_live(record):
                return False
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        return True


def _cmd_acquire(args: argparse.Namespace) -> int:
    holder: Record = {
        "repo": args.repo,
        "worktree": os.path.realpath(args.worktree),
        "branch": args.branch,
        "pid": args.pid,
        "target": args.target,
        "started": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }
    refused = acquire(args.lock_dir, args.repo, holder, args.lock_timeout)
    if refused is None:
        return EXIT_OK
    if isinstance(refused, _Corrupt):
        print(
            f"wt-singleton: corrupt holder record {holder_path(args.lock_dir, args.repo)} "
            f"(held by unknown): {APPROVAL_HINT}",
            file=sys.stderr,
        )
    else:
        print(f"wt-singleton: {args.repo} already has a live instance: {describe(refused)}", file=sys.stderr)
    return EXIT_REFUSED


def _cmd_release(args: argparse.Namespace) -> int:
    if release(args.lock_dir, args.repo, args.force, args.expect_pid, args.lock_timeout):
        return EXIT_OK
    print(
        f"wt-singleton: {args.repo} holder is live (or corrupt); not releasing without --force: "
        f"{APPROVAL_HINT}",
        file=sys.stderr,
    )
    return EXIT_REFUSED


def _cmd_status(args: argparse.Namespace) -> int:
    path = holder_path(args.lock_dir, args.repo)
    record = read_record(path)
    if isinstance(record, _Corrupt):
        print(f"{args.repo}: corrupt holder record {path} (held by unknown; {APPROVAL_HINT})")
    elif record is not None and is_live(record):
        print(f"{args.repo}: held: {describe(record)}")
    else:
        print(f"{args.repo}: no live holder")
    return EXIT_OK


def _cmd_is_live(args: argparse.Namespace) -> int:
    record = read_record(holder_path(args.lock_dir, args.repo))
    if record is not None and is_live(record, docker_only=args.docker_only):
        return EXIT_OK
    return EXIT_NOT_LIVE


def _repo_name(value: str) -> str:
    if not REPO_NAME_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError(f"invalid repo name {value!r}: expected [A-Za-z0-9._-]+")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wt_singleton", description="Per-repo singleton lock with a persistent holder record."
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    def add_common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--lock-dir", type=Path, required=True)
        sub.add_argument("--repo", type=_repo_name, required=True)

    def add_timeout(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--lock-timeout", type=float, default=DEFAULT_LOCK_TIMEOUT)

    acquire_parser = subcommands.add_parser("acquire", help="take the singleton or refuse")
    add_common(acquire_parser)
    add_timeout(acquire_parser)
    acquire_parser.add_argument("--worktree", required=True)
    acquire_parser.add_argument("--branch", required=True)
    acquire_parser.add_argument("--pid", type=int, required=True)
    acquire_parser.add_argument("--target", required=True)
    acquire_parser.set_defaults(handler=_cmd_acquire)

    release_parser = subcommands.add_parser("release", help="drop the holder record")
    add_common(release_parser)
    add_timeout(release_parser)
    release_parser.add_argument("--force", action="store_true")
    release_parser.add_argument("--expect-pid", type=int, default=None)
    release_parser.set_defaults(handler=_cmd_release)

    status_parser = subcommands.add_parser("status", help="print the holder or `no live holder`")
    add_common(status_parser)
    status_parser.set_defaults(handler=_cmd_status)

    live_parser = subcommands.add_parser("is-live", help="exit 0 when a live holder exists, else 5")
    add_common(live_parser)
    live_parser.add_argument("--docker-only", action="store_true")
    live_parser.set_defaults(handler=_cmd_is_live)
    return parser


def main(argv: list[str]) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except LockTimeout as timed_out:
        print(f"wt-singleton: {timed_out}", file=sys.stderr)
        return EXIT_LOCK_TIMEOUT
    except OSError as file_error:
        print(f"wt-singleton: {file_error}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
