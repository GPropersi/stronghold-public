#!/usr/bin/env python3
"""gh-failure-summary.py — distill `gh run view --log-failed` output.

A failing CI job's raw `--log-failed` stream is thousands of lines (setup,
dependency installs, post-job cleanup) around a few dozen that matter. This
reads that stream and prints only the evidence: per failing job, the failing
tests, their assertion lines and the result line — or, for anything that is not
a recognised test runner (lint, builds, audits), the `##[error]` lines plus the
tail of output just before the step failed.

Recognised shapes (per job, first match wins):
  pytest   FAILED/ERROR lines from "short test summary info", each failing
           test's `file:line: ExceptionType` location + up to 8 `E   ` lines
           from the FAILURES/ERRORS section (identical tracebacks print once,
           with a count), and the final `=== … in Xs ===`.
  vitest   `FAIL <file> > <test>` lines + up to 8 following assertion lines
           (plus the `❯ file:line:col` location), and the Test Files/Tests
           totals.
  fallback `##[error]` lines, recognised lint-tool finding lines (black, ruff,
           eslint, tsc, prettier, actionlint style), and the last 15 non-empty
           lines before `Process completed with exit code`.

Output is capped at 150 lines; the last line then says how many were dropped
and where the raw log is.

Usage:
  gh-failure-summary.py [<log-file>|-]          summarize (stdin when omitted or "-")
  gh-failure-summary.py --prune-dir <dir> --keep <N>
                                                 maintenance: keep the newest N
                                                 log-failed-*.txt files in <dir>
                                                 (by mtime), delete the rest

Stdlib only; called by gh-log-fetch.sh --summary.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

MAX_OUTPUT_LINES = 150
MAX_LINE_CHARS = 500
MAX_ASSERTION_LINES = 8
FALLBACK_TAIL_LINES = 15
MAX_SIGNAL_LINES = 30
UNKNOWN_STEP = "UNKNOWN STEP"
DEFAULT_JOB = "log"
PRUNE_GLOB = "log-failed-*.txt"

TIMESTAMP_RE = re.compile(r"^﻿?\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z ?")
# Real ESC sequences and gh's caret rendering of them ("^[[31m").
ANSI_RE = re.compile(r"(?:\x1b|\^\[)\[[0-9;?]*[A-Za-z]")
EXIT_LINE_RE = re.compile(r"Process completed with exit code \d+")

PYTEST_SUMMARY_HEADER_RE = re.compile(r"^=+ short test summary info =+$")
PYTEST_DETAIL_HEADER_RE = re.compile(r"^=+ (FAILURES|ERRORS) =+$")
PYTEST_ANY_HEADER_RE = re.compile(r"^=+ .+ =+$")
PYTEST_RESULT_RE = re.compile(r"^=+ .*\bin \d+(?:\.\d+)?s\b.* =+$")
PYTEST_OUTCOME_RE = re.compile(r"^(FAILED|ERROR) \S")
PYTEST_SECTION_RE = re.compile(r"^_+ (.+?) _+$")
PYTEST_LOCATION_RE = re.compile(r"^\S+:\d+: [A-Za-z_][\w.]*$")

VITEST_FAIL_RE = re.compile(r"^\s*FAIL\s+(\S+\.(?:test|spec)\.[cm]?[jt]sx?\b.*)$")
VITEST_LOCATION_RE = re.compile(r"^\s*❯ \S+:\d+:\d+\s*$")
VITEST_CODE_FRAME_RE = re.compile(r"^\s*\d*\s*\|")
VITEST_TOTALS_RE = re.compile(r"^\s*(Test Files|Tests)\s{2,}\S")

LINT_SIGNAL_RES = (
    re.compile(r"^would reformat \S"),  # black --check
    re.compile(r"^Would reformat: \S"),  # ruff format --check
    re.compile(r"^\S+:\d+:\d+: \S"),  # ruff / flake8 / actionlint / mypy
    re.compile(r"^\S+\(\d+,\d+\): error TS\d+"),  # tsc
    re.compile(r"^\s+\d+:\d+\s+(?:error|warning)\s"),  # eslint stylish
    re.compile(r"^\[warn\] \S"),  # prettier --check
)


class JobLog:
    """Cleaned message lines of one job, plus the step it failed in (if gh knew it)."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.lines: List[str] = []
        self.step: Optional[str] = None
        self._error_seen = False

    def add(self, step: str, message: str) -> None:
        # The step of the first ##[error] line wins; before it, the latest known step.
        if step and step != UNKNOWN_STEP and not self._error_seen:
            self.step = step
        if message.startswith("##[error]"):
            self._error_seen = True
        self.lines.append(message)


def clean_message(raw: str) -> str:
    """Drop the leading timestamp and every ANSI colour sequence."""
    message = TIMESTAMP_RE.sub("", raw.lstrip("﻿"), count=1)
    return ANSI_RE.sub("", message).rstrip()


def parse_jobs(text: str) -> List[JobLog]:
    """Split `<job>\\t<step>\\t<timestamp> <message>` lines into per-job logs (in order)."""
    jobs: Dict[str, JobLog] = {}
    for raw_line in text.splitlines():
        fields = raw_line.split("\t", 2)
        if len(fields) == 3:
            job_name, step, message = fields
        else:
            job_name, step, message = DEFAULT_JOB, "", raw_line
        job_name = job_name.lstrip("﻿").strip() or DEFAULT_JOB
        job = jobs.get(job_name)
        if job is None:
            job = JobLog(job_name)
            jobs[job_name] = job
        job.add(step.strip(), clean_message(message))
    return list(jobs.values())


def clip(line: str) -> str:
    if len(line) <= MAX_LINE_CHARS:
        return line
    return line[: MAX_LINE_CHARS - 1] + "…"


def summarize_pytest(lines: List[str]) -> Optional[List[str]]:
    outcomes: List[str] = []
    details: List[Tuple[str, Optional[str], List[str]]] = []
    result_line: Optional[str] = None
    in_summary = False
    in_details = False
    current: Optional[Tuple[str, List[Optional[str]], List[str]]] = None

    def flush() -> None:
        if current is not None:
            title, location_holder, error_lines = current
            details.append((title, location_holder[0], error_lines))

    for line in lines:
        if PYTEST_ANY_HEADER_RE.match(line):
            if PYTEST_RESULT_RE.match(line):
                result_line = line
            in_summary = bool(PYTEST_SUMMARY_HEADER_RE.match(line))
            now_details = bool(PYTEST_DETAIL_HEADER_RE.match(line))
            if in_details and not now_details:
                flush()
                current = None
            in_details = now_details
            continue
        if in_summary and PYTEST_OUTCOME_RE.match(line):
            outcomes.append(line)
            continue
        if in_details:
            section = PYTEST_SECTION_RE.match(line)
            if section and set(section.group(1)) - {"_", " "}:
                flush()
                current = (section.group(1), [None], [])
                continue
            if current is None:
                continue
            if PYTEST_LOCATION_RE.match(line):
                current[1][0] = line
            elif line.startswith("E ") and len(current[2]) < MAX_ASSERTION_LINES:
                current[2].append(line)
    if in_details:
        flush()

    if not outcomes and not details and result_line is None:
        return None
    # Identical tracebacks (e.g. one broken fixture erroring every test) print once,
    # with a count; the per-test names are already in the FAILED/ERROR lines above.
    groups: Dict[Tuple[Optional[str], Tuple[str, ...]], List[str]] = {}
    for title, location, error_lines in details:
        groups.setdefault((location, tuple(error_lines)), []).append(title)
    out: List[str] = list(outcomes)
    for (location, error_lines), titles in groups.items():
        if out:
            out.append("")
        out.append(f"- {titles[0]}")
        if location:
            out.append(f"  {location}")
        out.extend(f"  {error_line}" for error_line in error_lines)
        if len(titles) > 1:
            out.append(f"  (+{len(titles) - 1} more with this identical error)")
    if result_line:
        if out:
            out.append("")
        out.append(result_line)
    return out


def summarize_vitest(lines: List[str]) -> Optional[List[str]]:
    out: List[str] = []
    totals: List[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        failure = VITEST_FAIL_RE.match(line)
        if VITEST_TOTALS_RE.match(line):
            totals.append(" ".join(line.split()))
        if not failure:
            index += 1
            continue
        if out:
            out.append("")
        out.append(f"FAIL {failure.group(1).strip()}")
        assertion_count = 0
        index += 1
        while index < len(lines):
            follower = lines[index]
            if VITEST_FAIL_RE.match(follower) or "⎯" in follower or VITEST_CODE_FRAME_RE.match(follower):
                break
            if VITEST_LOCATION_RE.match(follower):
                out.append(f"  {follower.strip()}")
                index += 1
                break
            if follower.strip() and assertion_count < MAX_ASSERTION_LINES:
                out.append(f"  {follower.strip()}")
                assertion_count += 1
            index += 1
    if not out:
        return None
    if totals:
        out.append("")
        out.extend(totals)
    return out


def summarize_fallback(lines: List[str]) -> List[str]:
    exit_index = next(
        (index for index, line in enumerate(lines) if EXIT_LINE_RE.search(line)),
        len(lines),
    )
    error_lines = [line for line in lines if line.startswith("##[error]")]
    tail_source = [
        line
        for line in lines[:exit_index]
        if line.strip() and not (line.startswith("##[") and not line.startswith("##[warning]"))
    ]
    tail = tail_source[-FALLBACK_TAIL_LINES:]
    tail_set = set(tail)
    signals: List[str] = []
    for line in lines[:exit_index]:
        if line in tail_set or line in signals:
            continue
        if any(pattern.match(line) for pattern in LINT_SIGNAL_RES):
            signals.append(line)
            if len(signals) >= MAX_SIGNAL_LINES:
                break
    out: List[str] = list(error_lines)
    if signals:
        if out:
            out.append("")
        out.extend(signals)
    if tail:
        if out:
            out.append("")
        out.append(f"last {len(tail)} lines before the failure:")
        out.extend(tail)
    if not out:
        out.append("(no output captured for this job)")
    return out


def summarize_job(job: JobLog) -> List[str]:
    heading = f"## {job.name}" + (f" — {job.step}" if job.step else "")
    body = summarize_pytest(job.lines)
    if body is None:
        body = summarize_vitest(job.lines)
    if body is None:
        body = summarize_fallback(job.lines)
    return [heading] + body


def summarize(text: str, raw_path: Optional[str] = None) -> str:
    """Return the distilled summary of a `--log-failed` stream (newline-terminated)."""
    out: List[str] = []
    for job in parse_jobs(text):
        if out:
            out.append("")
        out.extend(summarize_job(job))
    if not out:
        out = ["(empty log)"]
    out = [clip(line) for line in out]
    if len(out) > MAX_OUTPUT_LINES:
        kept = out[: MAX_OUTPUT_LINES - 1]
        dropped = len(out) - len(kept)
        kept.append(f"… truncated ({dropped} more lines; raw log: {raw_path or '<stdin>'})")
        out = kept
    return "\n".join(out) + "\n"


def prune_logs(directory: str, keep: int) -> List[str]:
    """Keep the newest `keep` log-failed-*.txt files in `directory` by mtime; delete the rest."""
    if not os.path.isdir(directory):
        return []
    candidates = [path for path in glob.glob(os.path.join(directory, PRUNE_GLOB)) if os.path.isfile(path)]
    candidates.sort(key=lambda path: (os.path.getmtime(path), path), reverse=True)
    removed: List[str] = []
    for path in candidates[keep:]:
        try:
            os.remove(path)
        except FileNotFoundError:
            continue
        removed.append(path)
    return removed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gh-failure-summary.py",
        description="Distill `gh run view --log-failed` output into the failing jobs, tests and assertions.",
    )
    parser.add_argument("log_file", nargs="?", default="-", help="log file to summarize ('-' or omitted = stdin)")
    parser.add_argument("--prune-dir", help="maintenance mode: directory holding log-failed-*.txt files")
    parser.add_argument("--keep", type=int, help="with --prune-dir: how many of the newest files to keep")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.prune_dir is not None or args.keep is not None:
        if args.prune_dir is None or args.keep is None:
            parser.error("--prune-dir and --keep must be given together")
        if args.keep < 0:
            parser.error("--keep must be >= 0")
        if args.log_file != "-":
            parser.error("--prune-dir does not take a log file")
        prune_logs(args.prune_dir, args.keep)
        return 0
    if args.log_file == "-":
        text = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        raw_path = None
    else:
        try:
            with open(args.log_file, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError as exc:
            print(f"gh-failure-summary.py: cannot read {args.log_file}: {exc.strerror}", file=sys.stderr)
            return 2
        raw_path = args.log_file
    sys.stdout.write(summarize(text, raw_path=raw_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
