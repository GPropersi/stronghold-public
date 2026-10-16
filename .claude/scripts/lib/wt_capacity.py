"""wt_capacity: read-only machine capacity model for agent/test fan-out.

Vendored (stdlib only) from app-main/scripts/capacity.py's precedent: it
re-implements the meminfo/cgroup parsers and ceiling math rather than shelling
out, because `capacity.py show` is human text and `make capacity` writes. Cost
numbers come from app-main's `docker/.capacity.generated.env`; nothing here
writes a file or reads Docker.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

BYTES_PER_KB = 1024
BYTES_PER_GB = 1024**3

# Mirrors app-main/scripts/capacity.py:DEFAULT_MEM_SAFETY (parity-tested).
MEM_SAFETY = 0.90

# Fixed conservative per-agent memory budget (GB) for repos with no cost model.
DEFAULT_AGENT_GB = 2.5

REQUIRED_KEYS = (
    "APP_BASE_MB",
    "APP_WORKER_MB",
    "APP_SPOKE_IDLE_MB",
    "APP_HUB_IDLE_MB",
    "APP_USABLE_MB",
)

# Bounded so a pathological value is "corrupt", not an int-limit or float-overflow traceback.
_DECIMAL = re.compile(r"[0-9]{1,9}")


class CapacityFileError(Exception):
    """The capacity env file is missing a key, has a bad value, or a zero divisor."""


@dataclass(frozen=True)
class LiveMemory:
    available_bytes: int | None
    source: Literal["host", "none"]


def parse_meminfo(text: str, key: str) -> int | None:
    """Return `key`'s value from /proc/meminfo text in bytes, or None.

    A malformed value is treated like a missing key (unknown), not an error.
    """
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        if name == key:
            try:
                return int(rest.split()[0]) * BYTES_PER_KB
            except (IndexError, ValueError):
                return None
    return None


def parse_cgroup_max(text: str) -> int | None:
    """Return a cgroup v2 `memory.max` limit in bytes; None for `max` or garbage."""
    limit = text.strip()
    if limit == "max":
        return None
    try:
        return int(limit)
    except ValueError:
        return None


def parse_capacity_env(text: str) -> dict[str, int]:
    """Parse the REQUIRED `APP_*_MB` keys from a capacity env file; ignore the rest.

    Raises CapacityFileError naming the key for a missing/non-decimal value or a
    zero divisor (each of WORKER, SPOKE_IDLE and BASE+WORKER divides in the math).
    """
    raw: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name, sep, value = stripped.partition("=")
        if sep:
            raw[name.strip()] = value.strip()
    parsed: dict[str, int] = {}
    for key in REQUIRED_KEYS:
        value = raw.get(key)
        if value is None or not _DECIMAL.fullmatch(value):
            raise CapacityFileError(
                f"{key} is missing or not a decimal integer in the capacity file; run make capacity"
            )
        parsed[key] = int(value)
    if parsed["APP_BASE_MB"] + parsed["APP_WORKER_MB"] <= 0:
        raise CapacityFileError(
            "APP_WORKER_MB: APP_BASE_MB + APP_WORKER_MB must be > 0; run make capacity"
        )
    for key in ("APP_WORKER_MB", "APP_SPOKE_IDLE_MB"):
        if parsed[key] == 0:
            raise CapacityFileError(f"{key} is 0 (a divisor); run make capacity")
    return parsed


def _read_optional(path: Path) -> str | None:
    try:
        return path.read_text()
    except (OSError, UnicodeDecodeError):
        return None


def read_live_available(meminfo_path: Path, cgroup_path: Path) -> LiveMemory:
    """Host MemAvailable (bytes), capped by the cgroup limit; unknown -> source none."""
    meminfo_text = _read_optional(meminfo_path)
    available = parse_meminfo(meminfo_text, "MemAvailable") if meminfo_text else None
    if available is None:
        return LiveMemory(available_bytes=None, source="none")
    cgroup_text = _read_optional(cgroup_path)
    cgroup_max = parse_cgroup_max(cgroup_text) if cgroup_text else None
    if cgroup_max is not None:
        available = min(available, cgroup_max)
    return LiveMemory(available_bytes=available, source="host")


def live_usable_gb(live: LiveMemory) -> float | None:
    if live.available_bytes is None:
        return None
    return live.available_bytes * MEM_SAFETY / BYTES_PER_GB


def min_run_gb(env: dict[str, int]) -> float:
    return (env["APP_BASE_MB"] + env["APP_WORKER_MB"]) / 1024


def _floor(value: float) -> int:
    # Round before flooring so binary-float noise cannot drop an exact multiple.
    return max(0, math.floor(round(value, 6)))


def spokes_ceiling(usable_gb: float, env: dict[str, int]) -> int:
    hub_idle = env["APP_HUB_IDLE_MB"] / 1024
    spoke_idle = env["APP_SPOKE_IDLE_MB"] / 1024
    return _floor((usable_gb - hub_idle - min_run_gb(env)) / spoke_idle)


def suites_ceiling(usable_gb: float, env: dict[str, int]) -> int:
    return _floor(usable_gb / min_run_gb(env))


def agents_max(spokes: int, suites: int) -> int:
    return max(1, min(spokes, suites))


def default_agents_max(usable_gb: float | None) -> int:
    if usable_gb is None:
        return 1
    return max(1, _floor(usable_gb / DEFAULT_AGENT_GB))


# ---------------------------------------------------------------------------
# Reporter (CLI). The lib never reads seams or enumerates repos: wt-capacity.sh
# does, and passes `--repo NAME=POLICY` pairs.
# ---------------------------------------------------------------------------

NO_MODEL = "n/a (no cost model declared)"
COST_REPO = "app-main"
DEFAULT_MEMINFO = "/proc/meminfo"
DEFAULT_CGROUP = "/sys/fs/cgroup/memory.max"
CAPACITY_FILE_RELATIVE = Path("app-main") / "docker" / ".capacity.generated.env"
TABLE_HEADER = ("repo", "policy", "min_run_gb", "hub_idle_gb", "spoke_idle_gb", "stack_ceiling")
COLUMN_GAP = "  "
MISSING_FILE_HINT = "run make -C app-main capacity"


@dataclass(frozen=True)
class Report:
    usable_gb: float | None
    source: str
    spokes: int | str
    suites: int | str
    agents: int
    notes: list[str]
    cost: dict[str, float] | None
    repos: list[dict]


def _cost_object(model: dict[str, int]) -> dict[str, float]:
    return {
        "min_run_gb": round(min_run_gb(model), 3),
        "hub_idle_gb": round(model["APP_HUB_IDLE_MB"] / 1024, 3),
        "spoke_idle_gb": round(model["APP_SPOKE_IDLE_MB"] / 1024, 3),
    }


def _load_capacity(path: Path) -> tuple[dict[str, int] | None, str | None]:
    """Return (model, None) or (None, note) for a missing/unreadable/corrupt file."""
    text = _read_optional(path)
    if text is None:
        return None, f"capacity file {path} is missing or unreadable; {MISSING_FILE_HINT}"
    try:
        return parse_capacity_env(text), None
    except CapacityFileError as error:
        return None, f"capacity file {path} is corrupt: {error}; {MISSING_FILE_HINT}"


def _stack_ceiling(
    policy: str, repo: str, model: dict[str, int] | None, usable_gb: float | None
) -> int | str:
    if policy == "build-only":
        return 0
    if policy == "singleton-runtime":
        return 1
    if policy == "full" and repo == COST_REPO and model is not None and usable_gb is not None:
        return spokes_ceiling(usable_gb, model)
    return "n/a"


def _build_rows(
    pairs: list[tuple[str, str]],
    scope: str | None,
    model: dict[str, int] | None,
    usable_gb: float | None,
) -> list[dict]:
    rows = list(pairs)
    if scope is not None and scope not in {name for name, _ in rows}:
        rows.append((scope, ""))
    result = []
    for name, raw_policy in rows:
        policy = raw_policy if raw_policy else "n/a"
        row_model = model if name == COST_REPO else None
        result.append(
            {
                "repo": name,
                "policy": policy,
                "cost": _cost_object(row_model) if row_model else None,
                "stack_ceiling": _stack_ceiling(policy, name, row_model, usable_gb),
            }
        )
    return result


def build_report(
    live: LiveMemory,
    capacity_path: Path,
    pairs: list[tuple[str, str]],
    scope: str | None,
) -> Report:
    """Apply the output-contract rules: scope -> cost model, degraded modes, per-repo rows."""
    notes: list[str] = []
    live_gb = live_usable_gb(live)
    # Only app-main (or an unscoped call) uses the APP_* cost model; any other name has none.
    uses_cost_model = scope is None or scope == COST_REPO
    model, model_note = _load_capacity(capacity_path)

    usable_gb = live_gb
    source = live.source
    spokes: int | str = "n/a"
    suites: int | str = "n/a"
    cost: dict[str, float] | None = None
    if uses_cost_model and model_note:
        notes.append(model_note)
    if uses_cost_model and model is not None:
        if usable_gb is None:
            usable_gb = model["APP_USABLE_MB"] / 1024
            source = "static"
            notes.append("host memory (/proc/meminfo) is unreadable; using static APP_USABLE_MB")
        spokes = spokes_ceiling(usable_gb, model)
        suites = suites_ceiling(usable_gb, model)
        agents = agents_max(spokes, suites)
        cost = _cost_object(model)
    else:
        agents = default_agents_max(live_gb)
        if live_gb is None:
            notes.append("host memory (/proc/meminfo) is unreadable; agents_max=1")
    return Report(
        usable_gb=usable_gb,
        source=source,
        spokes=spokes,
        suites=suites,
        agents=agents,
        notes=notes,
        cost=cost,
        repos=_build_rows(pairs, scope, model, usable_gb),
    )


def render_json(report: Report) -> str:
    payload = {
        "live_usable_gb": "unavailable" if report.usable_gb is None else round(report.usable_gb, 3),
        "source": report.source,
        "spokes_ceiling": report.spokes,
        "suites_ceiling": report.suites,
        "agents_max": report.agents,
        "notes": report.notes,
        "repos": report.repos,
        "cost": report.cost,
    }
    return json.dumps(payload, indent=2)


def render_table(rows: list[dict]) -> list[str]:
    """Aligned columns; a no-cost row collapses its three cost columns into one cell."""
    body: list[tuple[list[str], bool]] = []
    for row in rows:
        cost = row["cost"]
        if cost is None:
            cost_cells = [NO_MODEL]
        else:
            cost_cells = [f"{cost[key]:.3f}" for key in ("min_run_gb", "hub_idle_gb", "spoke_idle_gb")]
        body.append(([row["repo"], row["policy"], *cost_cells, str(row["stack_ceiling"])], cost is None))
    widths = [len(title) for title in TABLE_HEADER]
    for cells, collapsed in body:
        widths[0] = max(widths[0], len(cells[0]))
        widths[1] = max(widths[1], len(cells[1]))
        widths[5] = max(widths[5], len(cells[-1]))
        if not collapsed:
            for index in (2, 3, 4):
                widths[index] = max(widths[index], len(cells[index]))
    cost_span = sum(widths[2:5]) + 2 * len(COLUMN_GAP)
    if any(collapsed for _, collapsed in body) and cost_span < len(NO_MODEL):
        widths[4] += len(NO_MODEL) - cost_span
        cost_span = len(NO_MODEL)
    lines = [COLUMN_GAP.join(title.ljust(width) for title, width in zip(TABLE_HEADER, widths)).rstrip()]
    for cells, collapsed in body:
        if collapsed:
            padded = [cells[0].ljust(widths[0]), cells[1].ljust(widths[1]), cells[2].ljust(cost_span)]
        else:
            padded = [cell.ljust(width) for cell, width in zip(cells[:5], widths[:5])]
        lines.append(COLUMN_GAP.join([*padded, cells[-1]]).rstrip())
    return lines


def render_text(report: Report) -> str:
    usable = "unavailable" if report.usable_gb is None else f"{report.usable_gb:.3f}"
    lines = render_table(report.repos)
    lines += [
        "",
        f"live_usable_gb={usable}",
        f"source={report.source}",
        f"spokes_ceiling={report.spokes}",
        f"suites_ceiling={report.suites}",
        f"agents_max={report.agents}",
    ]
    lines.extend(f"note: {note}" for note in report.notes)
    return "\n".join(lines)


def _parse_repo_pair(value: str) -> tuple[str, str]:
    name, separator, policy = value.partition("=")
    if not separator or not name:
        raise argparse.ArgumentTypeError(f"--repo expects NAME=POLICY, got {value!r}")
    return name, policy


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wt_capacity.py",
        description="Read-only worktree capacity report (never writes, never reads Docker).",
    )
    parser.add_argument("repo_dir", nargs="?", default=None, help="scope to one repo directory name")
    parser.add_argument("--meminfo", default=DEFAULT_MEMINFO)
    parser.add_argument("--cgroup", default=DEFAULT_CGROUP)
    parser.add_argument("--capacity-file", default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--repo", action="append", default=[], type=_parse_repo_pair, metavar="NAME=POLICY")
    return parser


def main(argv: list[str], env: Mapping[str, str]) -> int:
    """Exit 0 ok (degraded modes included, with a `note:` line), 2 usage."""
    try:
        args = _build_parser().parse_args(argv)
    except SystemExit as exit_request:
        return int(exit_request.code or 0)
    stronghold = Path(env.get("STRONGHOLD_DIR") or Path(__file__).resolve().parents[3])
    capacity_path = Path(args.capacity_file) if args.capacity_file else stronghold / CAPACITY_FILE_RELATIVE
    live = read_live_available(Path(args.meminfo), Path(args.cgroup))
    scope = os.path.basename(args.repo_dir.rstrip("/")) if args.repo_dir else None
    report = build_report(live, capacity_path, args.repo, scope or None)
    print(render_json(report) if args.as_json else render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], os.environ))
