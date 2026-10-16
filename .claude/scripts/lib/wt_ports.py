"""Host-port resolution for a worktree's services, generalised to N `ENV_KEY=BASE` services.

Lifted from app-main/scripts/spoke_ports.py (which keeps its own copy: it is NOT migrated onto
this one). Only the service table changes: app-main hard-codes web + vite, this takes any number
of `--service ENV_KEY=BASE` pairs. Stdlib only.

CLI (see `main`):
  wt_ports.py resolve --project P --slug S [--primary] --service KEY=BASE ... --output FILE
  wt_ports.py show --output FILE

Resolution, per service, in `--service` order:
1. An explicit port (the process environment variable named by the service's ENV_KEY) wins; if
   something else holds it, fail, naming the holder. It is cached like any other port.
2. Else the cached port (a `KEY=port` line already in the output file) is kept while it is free
   or published by this project.
3. Else walk upward from the preferred port (the base for the primary checkout,
   base + crc32(slug) % 99 + 1 for a worktree) to the first free one, within base..base+199.
   The walk never wraps below the preferred port.

"Free" means no OTHER compose project publishes the port (`docker ps --filter publish=<port>`)
and a test bind on 0.0.0.0 succeeds (non-Docker listeners). A port this project already
publishes counts as free even though the bind fails.

Claimed-set rule (asymmetric, order-dependent, exactly as in spoke_ports.py): for the service
being resolved, claimed = every already-resolved service's port, PLUS, only when this service's
own value is not explicit, every OTHER service's explicit value, wherever it falls in the
`--service` order. Explicit values are all known upfront, so an auto-resolved service listed
earlier still avoids a later service's explicit port. Two equal explicit ports fail on the
later service.

Test seam: `WT_BUSY_PORTS=8660,8661` makes those ports read as "bind fails" (a host process),
so tests never depend on host port state.

Any docker failure (including `docker` missing from PATH) exits 1 with `wt ports: ...` on
stderr and writes no file; a bind error other than EADDRINUSE surfaces rather than reading as
free.
"""

from __future__ import annotations

import argparse
import errno
import os
import re
import socket
import subprocess
import sys
import tempfile
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

# A worktree prefers base + 1..99, leaving the base port to the primary checkout.
WORKTREE_OFFSET_SPAN: int = 99
# Candidates walked per service: base..base+199.
PORT_RANGE_SIZE: int = 200
DOCKER_PS_TIMEOUT_SECONDS: int = 30
COMPOSE_PROJECT_LABEL: str = "com.docker.compose.project"
HOST_PROCESS_HOLDER: str = "a host process"
UNLABELLED_CONTAINER_HOLDER: str = "a container outside any compose project"
BUSY_PORTS_ENV: str = "WT_BUSY_PORTS"
ENV_KEY_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAX_HELD_LISTED: int = 5

OwnerFn = Callable[[int], str | None]
BindableFn = Callable[[int], bool]


class PortUnavailable(RuntimeError):
    """No usable port: an explicit port is held, or the whole range is."""


class DockerPsError(RuntimeError):
    """`docker ps` could not answer, so a port's owner is unknown."""


@dataclass(frozen=True)
class Service:
    env_key: str
    base_port: int


@dataclass(frozen=True)
class Resolution:
    port: int
    note: str | None


# --- real probes ---------------------------------------------------------------


def docker_port_owner(port: int) -> str | None:
    """The compose project of a running container publishing `port`, else None.

    A container outside any compose project reports a generic holder name. Any
    `docker ps` failure raises: an unknown owner must never read as "free".
    """
    try:
        result = subprocess.run(
            [
                "docker",
                "ps",
                "--filter",
                f"publish={port}",
                "--format",
                f'{{{{.Label "{COMPOSE_PROJECT_LABEL}"}}}}',
            ],
            capture_output=True,
            text=True,
            timeout=DOCKER_PS_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as timed_out:
        raise DockerPsError(
            f"docker ps timed out after {DOCKER_PS_TIMEOUT_SECONDS}s"
        ) from timed_out
    except OSError as launch_error:
        raise DockerPsError(str(launch_error)) from launch_error
    if result.returncode != 0:
        raise DockerPsError(
            result.stderr.strip() or f"docker ps exited {result.returncode}"
        )
    holders = result.stdout.splitlines()
    if not holders:
        return None
    return next((holder for holder in holders if holder), UNLABELLED_CONTAINER_HOLDER)


def busy_ports_from_env() -> set[int]:
    """Ports the WT_BUSY_PORTS test seam declares as unbindable (comma list)."""
    raw = os.environ.get(BUSY_PORTS_ENV, "")
    busy: set[int] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if not (token.isascii() and token.isdecimal()):
            raise PortUnavailable(
                f"{BUSY_PORTS_ENV} entry {token!r} is not a port number"
            )
        busy.add(int(token))
    return busy


def port_is_bindable(port: int) -> bool:
    """Whether a fresh TCP socket can bind 0.0.0.0:`port`.

    Ports listed in WT_BUSY_PORTS read as unbindable without touching the host. Otherwise only
    EADDRINUSE means "held"; any other bind error (e.g. EACCES on a privileged port)
    propagates so `main` reports its real message.
    """
    if port in busy_ports_from_env():
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_socket:
        # SO_REUSEADDR on, so a TIME_WAIT leftover from this worktree's own previous run (after a
        # `down`) doesn't read as "held" and move the URL. Linux still refuses the bind while any
        # socket LISTENs on the port (Docker's proxy also binds with SO_REUSEADDR).
        probe_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe_socket.bind(("0.0.0.0", port))
        except OSError as bind_error:
            if bind_error.errno == errno.EADDRINUSE:
                return False
            raise
    return True


# --- resolution ------------------------------------------------------------------


def preferred_port(base_port: int, slug: str, primary: bool) -> int:
    if primary:
        return base_port
    return base_port + zlib.crc32(slug.encode()) % WORKTREE_OFFSET_SPAN + 1


def _holder(
    port: int, project: str, owner_fn: OwnerFn, bindable_fn: BindableFn
) -> str | None:
    """Who holds `port` against this project, or None when it is usable."""
    owner = owner_fn(port)
    if owner == project:
        return None
    if owner is not None:
        return owner
    return None if bindable_fn(port) else HOST_PROCESS_HOLDER


def resolve_service(
    service: Service,
    project: str,
    slug: str,
    primary: bool,
    explicit_port: int | None,
    cached_port: int | None,
    claimed: set[int],
    owner_fn: OwnerFn,
    bindable_fn: BindableFn,
) -> Resolution:
    """Pick `service`'s host port; `claimed` holds ports given to (or explicitly set for) another service."""

    def holder_of(port: int) -> str | None:
        if port in claimed:
            return f"this spoke's other service ({port} is claimed by it)"
        return _holder(port, project, owner_fn, bindable_fn)

    if explicit_port is not None:
        holder = holder_of(explicit_port)
        if holder is not None:
            raise PortUnavailable(
                f"{service.env_key}={explicit_port} is held by {holder}; "
                f"free it or choose another {service.env_key}"
            )
        return Resolution(explicit_port, None)

    first_rejected: tuple[int, str] | None = None
    if cached_port is not None:
        holder = holder_of(cached_port)
        if holder is None:
            return Resolution(cached_port, None)
        first_rejected = (cached_port, holder)

    start = preferred_port(service.base_port, slug, primary)
    last = service.base_port + PORT_RANGE_SIZE - 1
    holders: list[tuple[int, str]] = []
    for candidate in range(start, last + 1):
        holder = holder_of(candidate)
        if holder is None:
            note = None
            if first_rejected is None and holders:
                first_rejected = holders[0]
            if first_rejected is not None:
                rejected_port, rejected_holder = first_rejected
                note = (
                    f"{rejected_port} was held by {rejected_holder}; using {candidate}"
                )
            return Resolution(candidate, note)
        holders.append((candidate, holder))
    shown = ", ".join(f"{port}={holder}" for port, holder in holders[:MAX_HELD_LISTED])
    more = (
        f", and {len(holders) - MAX_HELD_LISTED} more"
        if len(holders) > MAX_HELD_LISTED
        else ""
    )
    raise PortUnavailable(
        f"no free {service.env_key} port in {start}..{last} "
        f"(held: {shown}{more}); set {service.env_key}=<port> to pick one explicitly"
    )


# --- cache file --------------------------------------------------------------------


def read_cache(path: Path) -> dict[str, int]:
    """Cached ports by env key. A missing or malformed entry is simply absent: it's a cache."""
    if not path.exists():
        return {}
    cached: dict[str, int] = {}
    for line in path.read_text().splitlines():
        key, separator, raw_value = line.partition("=")
        if separator and raw_value.isascii() and raw_value.isdecimal():
            port = int(raw_value)
            if 0 < port < 65536:
                cached[key.strip()] = port
    return cached


def render_cache(services: tuple[Service, ...], ports: dict[str, int]) -> str:
    return "".join(f"{service.env_key}={ports[service.env_key]}\n" for service in services)


def _write_atomically(path: Path, content: str) -> None:
    """Write via a same-directory temp file + os.replace, so readers never see a partial file.

    The file keeps NamedTemporaryFile's owner-only 0600 mode: only host-side make/compose
    (running as this user) read it.
    """
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(content)
        os.replace(temp_path, path)
    except BaseException:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise


def _ports_line(ports: dict[str, int]) -> str:
    return "  ".join(f"{env_key}={port}" for env_key, port in ports.items())


# --- subcommands -------------------------------------------------------------------


def _explicit_ports(
    services: tuple[Service, ...], environ: Mapping[str, str]
) -> dict[str, int | None]:
    """Each service's explicit port from the environment (unset or empty -> None)."""
    explicit: dict[str, int | None] = {}
    for service in services:
        raw = environ.get(service.env_key, "").strip()
        if not raw:
            explicit[service.env_key] = None
            continue
        try:
            explicit[service.env_key] = _port(raw)
        except argparse.ArgumentTypeError as bad_value:
            raise PortUnavailable(f"{service.env_key}: {bad_value}") from bad_value
    return explicit


def _resolve(
    args: argparse.Namespace, owner_fn: OwnerFn, bindable_fn: BindableFn
) -> int:
    services: tuple[Service, ...] = tuple(args.service)
    cached = read_cache(args.output)
    explicit = _explicit_ports(services, os.environ)
    ports: dict[str, int] = {}
    notes: list[str] = []
    for service in services:
        explicit_port = explicit[service.env_key]
        # An auto-resolved service also avoids every other service's explicit port, so an
        # explicit port equal to another service's cached/preferred port moves that service
        # instead of failing this one. Two equal explicit ports still fail (on the later one).
        claimed = set(ports.values())
        if explicit_port is None:
            claimed |= {
                port
                for env_key, port in explicit.items()
                if env_key != service.env_key and port is not None
            }
        resolution = resolve_service(
            service,
            project=args.project,
            slug=args.slug,
            primary=args.primary,
            explicit_port=explicit_port,
            cached_port=cached.get(service.env_key),
            claimed=claimed,
            owner_fn=owner_fn,
            bindable_fn=bindable_fn,
        )
        ports[service.env_key] = resolution.port
        if resolution.note is not None:
            notes.append(f"{service.env_key}: {resolution.note}")
    _write_atomically(args.output, render_cache(services, ports))
    for note in notes:
        print(note)
    print(f"ports: {_ports_line(ports)}")
    return 0


def _show(output: Path) -> int:
    cached = read_cache(output)
    if not cached:
        print("ports: not resolved yet")
        return 0
    print(f"ports: {_ports_line(cached)}")
    return 0


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as bad_value:
        raise argparse.ArgumentTypeError(
            f"expected a port number, got {value!r}"
        ) from bad_value
    if not 0 < port < 65536:
        raise argparse.ArgumentTypeError(f"port {port} is outside 1..65535")
    return port


def _service(value: str) -> Service:
    env_key, separator, raw_base = value.partition("=")
    if not separator or not ENV_KEY_PATTERN.match(env_key):
        raise argparse.ArgumentTypeError(f"expected ENV_KEY=BASE, got {value!r}")
    base_port = _port(raw_base)
    if base_port + PORT_RANGE_SIZE - 1 > 65535:
        raise argparse.ArgumentTypeError(
            f"{env_key}: base {base_port} leaves no room for {PORT_RANGE_SIZE} candidate ports"
        )
    return Service(env_key, base_port)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wt_ports",
        description="Resolve collision-free host ports for a worktree's services.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    resolve_parser = subcommands.add_parser(
        "resolve", help="probe, pick and cache the worktree's host ports"
    )
    resolve_parser.add_argument("--project", required=True)
    resolve_parser.add_argument("--slug", required=True)
    resolve_parser.add_argument("--primary", action="store_true")
    resolve_parser.add_argument(
        "--service",
        action="append",
        type=_service,
        required=True,
        metavar="ENV_KEY=BASE",
        help="repeatable; the env var of the same name holds an explicit port",
    )
    resolve_parser.add_argument("--output", type=Path, required=True)

    show_parser = subcommands.add_parser("show", help="print the cached ports")
    show_parser.add_argument("--output", type=Path, required=True)
    return parser


def main(
    argv: list[str],
    owner_fn: OwnerFn = docker_port_owner,
    bindable_fn: BindableFn = port_is_bindable,
) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "resolve":
        env_keys = [service.env_key for service in args.service]
        duplicates = sorted({key for key in env_keys if env_keys.count(key) > 1})
        if duplicates:
            parser.error(f"duplicate --service ENV_KEY: {', '.join(duplicates)}")
    try:
        if args.command == "show":
            return _show(args.output)
        return _resolve(args, owner_fn, bindable_fn)
    except PortUnavailable as unavailable:
        print(f"wt ports: {unavailable}", file=sys.stderr)
        return 1
    except DockerPsError as docker_error:
        print(f"wt ports: docker ps failed — {docker_error}", file=sys.stderr)
        return 1
    except (OSError, UnicodeDecodeError) as file_error:
        print(f"wt ports: {file_error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
