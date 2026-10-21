#!/usr/bin/env python3
"""Aggregation layer of the plans dashboard (stdlib only, pure functions, no HTTP).

list_plans(plans_dir, include_completed) -> {"repos": [{"repo", "plans": [Summary, ...]}]}
plan_detail(plans_dir, repo, state, topic, name) -> Summary keys + "steps", or None

Plans live at <plans_dir>/<repo>/{open,completed}/<topic>/<name>.md. Counting follows plan-status.sh
(done = ticked boxes, total = done + open, unticked post-merge / manual-post-merge / deferred boxes
are held and reported separately). Fail closed: every path that is read must resolve inside
plans_dir; names the detail URL could not address are skipped; internal frontmatter keys
(github_issue_node_id, worktree) are never emitted.

The second half is the read-only HTTP layer (make_server / main): GET and HEAD only, Host allowlist
(DNS-rebinding defence), loopback bind by default, JSON error bodies, no-store headers.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import socket
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import NamedTuple
from urllib.parse import parse_qs, unquote

import plan_backfill
import plan_lifecycle
import plan_parse
import plan_pr_link

SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
STATES = plan_parse.STATES
NON_PLAN_TYPES = plan_backfill.NON_PLAN_TYPES
NON_BRANCHES = plan_lifecycle.NON_BRANCHES
MASTER_KINDS = ("master", "orchestrator")
SUFFIX_INFERRED = ("-design", "-progress", "-master")
DONE_WORDS = ("done", "merged", "moved")


def _valid_segment(value: str) -> bool:
    return bool(SEGMENT_RE.fullmatch(value)) and value not in (".", "..")


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return False
    return True


def _children(directory: Path) -> list[Path]:
    try:
        return sorted(directory.iterdir(), key=lambda p: p.name)
    except OSError:
        return []


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _safe_url(url: object) -> str | None:
    return url if isinstance(url, str) and url.startswith(("http://", "https://")) else None


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return []


def _title(lines: list[str], name: str) -> str:
    """First H1 outside fenced code blocks (and outside frontmatter), else the file stem."""
    fence: str | None = None
    for line in lines[plan_parse._split_frontmatter(lines)[1] :]:
        fenced = plan_parse.FENCE_RE.match(line)
        if fence is not None:
            if fenced and fenced.group(1)[0] == fence[0] and len(fenced.group(1)) >= len(fence):
                fence = None
            continue
        if fenced:
            fence = fenced.group(1)
            continue
        if line.startswith("# ") and line[2:].strip():
            return line[2:].strip()
    return name


BLURB_MAX = 240
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def _blurb(lines: list[str]) -> str | None:
    """Fallback one-to-two sentence blurb: the first prose paragraph under `## Summary`."""
    fence: str | None = None
    in_summary = False
    para: list[str] = []
    for line in lines[plan_parse._split_frontmatter(lines)[1] :]:
        fenced = plan_parse.FENCE_RE.match(line)
        if fence is not None:
            if fenced and fenced.group(1)[0] == fence[0] and len(fenced.group(1)) >= len(fence):
                fence = None
            continue
        if fenced:
            fence = fenced.group(1)
            continue
        if line.startswith("## "):
            if in_summary:
                break
            in_summary = line[3:].strip().lower() == "summary"
            continue
        if not in_summary:
            continue
        text = line.strip()
        if para:
            prose = bool(text) and text[0] not in "-*|>#"
        else:
            prose = bool(text) and text[0] not in "-*|>#`[" and not text.startswith(("Mockup", "**"))
        if prose:
            para.append(text)
        elif para:
            break
    joined = re.sub(r"[`*]", "", " ".join(para))
    if len(joined) < 40:
        return None
    out = " ".join(_SENTENCE_END.split(joined)[:2])
    if len(out) > BLURB_MAX:
        cut = out.rfind(" ", 0, BLURB_MAX - 1)
        out = out[: cut if cut > 60 else BLURB_MAX - 1].rstrip(" ,;:") + "…"
    return out


PRS_MAX = 20


def _prs(raw: str | None, done_raw: str | None = None) -> list[dict]:
    """Pull-request links from the `pr_urls:` frontmatter value; anything not a GitHub PR URL is dropped.

    `done` is True for a URL listed in `pr_done:` (merged or closed, written by plan-pr-sync.sh)."""
    out: list[dict] = []
    done = set((done_raw or "").strip("\"'").split())
    for url in (raw or "").strip("\"'").split():
        if plan_pr_link.PR_URL_RE.fullmatch(url) and all(item["url"] != url for item in out):
            parts = url.split("/")
            out.append({"url": url, "number": int(parts[-1]), "repo": f"{parts[3]}/{parts[4]}", "done": url in done})
        if len(out) == PRS_MAX:
            break
    return out


def _kind(path: Path, doc: plan_parse.PlanDoc, lines: list[str]) -> str | None:
    """'plan' | 'master' | 'orchestrator', or None when the file is a filtered non-plan."""
    if doc.type in NON_PLAN_TYPES:
        return None
    if doc.type in MASTER_KINDS:
        return doc.type
    if doc.type is None and path.stem.lower().endswith(SUFFIX_INFERRED):
        inferred = plan_backfill.infer_type(path, lines)
        if inferred in NON_PLAN_TYPES:
            return None
        if inferred in MASTER_KINDS:
            return inferred
    return "plan"


def _boxes(doc: plan_parse.PlanDoc) -> list[plan_parse.Box]:
    return [box for step in doc.steps for box in step.boxes] + doc.stray_boxes


def _tally(boxes: list[plan_parse.Box]) -> tuple[int, int, dict]:
    done = sum(1 for b in boxes if b.state == "done")
    opened = sum(1 for b in boxes if b.state == "open")
    held = {
        "post_merge": sum(1 for b in boxes if b.state == "post-merge"),
        "manual_post_merge": sum(1 for b in boxes if b.state == "manual-post-merge"),
        "deferred": sum(1 for b in boxes if b.state == "deferred"),
    }
    return done, done + opened, held


def _status_word(status: str | None) -> str | None:
    tokens = (status or "").split()
    word = tokens[0].strip("`*_").rstrip(":.,;").lower() if tokens else ""
    return word or None


def _progress(doc: plan_parse.PlanDoc) -> tuple[int, int, dict, str]:
    done, total, held = _tally(_boxes(doc))
    if total == 0 and any(_status_word(step.status) for step in doc.steps):
        done = sum(1 for step in doc.steps if _status_word(step.status) in DONE_WORDS)
        return done, len(doc.steps), held, "steps"
    return done, total, held, "boxes"


def _load(path: Path) -> tuple[plan_parse.PlanDoc, list[str], str | None]:
    doc = plan_parse.parse_plan(path)
    lines = _read_lines(path)
    return doc, lines, _kind(path, doc, lines)


def _topic_plans(topic_dir: Path, root: Path) -> list[Path]:
    """Addressable plan files at the root of a topic directory, sorted by name."""
    found = []
    for path in _children(topic_dir):
        if path.suffix != ".md" or not _valid_segment(path.stem):
            continue
        if _is_file(path) and _inside(path, root):
            found.append(path)
    return sorted(found, key=lambda p: p.stem)


def _dangling() -> dict:
    return {"repo": None, "state": None, "topic": None, "name": None, "kind": "dangling", "done": None, "total": None}


def _child_from_path(path: Path, root: Path, kind: str) -> dict | None:
    """Child record for a plan file at exactly <repo>/<state>/<topic>/<name>.md, else None (dangling)."""
    try:
        parts = (path.parent.resolve() / path.name).relative_to(root).parts
    except (OSError, RuntimeError, ValueError):
        return None
    if len(parts) != 4 or parts[1] not in STATES or not parts[3].endswith(".md"):
        return None
    repo, state, topic, name = parts[0], parts[1], parts[2], parts[3][:-3]
    if not all(_valid_segment(p) for p in (repo, topic, name)):
        return None
    if not _is_file(path) or not _inside(path, root):
        return None
    doc, _lines, plan_kind = _load(path)
    if plan_kind is None:
        return None
    done, total, _held, _basis = _progress(doc)
    return {"repo": repo, "state": state, "topic": topic, "name": name, "kind": kind, "done": done, "total": total}


def _resolve_child(master: Path, step: plan_parse.Step, root: Path) -> dict | None:
    link = step.sub_plan_link
    if link and link != "none":
        resolved = plan_parse.resolve_link(master, link)
        if resolved.path is None or resolved.kind == "dangling":
            return _dangling()
        return _child_from_path(resolved.path, root, resolved.kind) or _dangling()
    branch = (step.branch or "").strip()
    if branch in NON_BRANCHES or branch.startswith("TBD"):
        return None
    for sibling in _topic_plans(master.parent, root):
        if sibling == master:
            continue
        doc, _lines, plan_kind = _load(sibling)
        if plan_kind is not None and doc.frontmatter.get("branch") == branch:
            return _child_from_path(sibling, root, "ok")
    return None


def _build(path: Path, root: Path, repo: str, state: str, topic: str, name: str) -> dict | None:
    doc, lines, kind = _load(path)
    if kind is None:
        return None
    done, total, held, basis = _progress(doc)
    issue = doc.github_issue
    drift: list[str] = []
    if state == "completed" and doc.finished is False:
        drift.append("completed but finished: false")
    steps = []
    for step in doc.steps:
        child = _resolve_child(path, step, root)
        if child is not None and child["kind"] == "dangling":
            drift.append(f"dangling sub-plan link in step {step.n}")
        s_done, s_total, s_held = _tally(step.boxes)
        steps.append(
            {
                "n": step.n,
                "title": step.title,
                "done": s_done,
                "total": s_total,
                "held": s_held,
                "status": step.status,
                "status_word": _status_word(step.status),
                "branch": step.branch,
                "child": child,
                "summary": list(step.summary),
                "boxes": [{"state": box.state, "text": box.text} for box in step.boxes],
            }
        )
    return {
        "repo": repo,
        "state": state,
        "topic": topic,
        "name": name,
        "kind": kind,
        "title": _title(lines, name),
        "short_title": (doc.frontmatter.get("short_title") or "").strip().strip("\"'") or None,
        "prs": _prs(doc.frontmatter.get("pr_urls"), doc.frontmatter.get("pr_done")),
        "about":(doc.frontmatter.get("short_summary") or "").strip().strip("\"'") or _blurb(lines),
        "branch": doc.frontmatter.get("branch") or None,
        "issue": None if issue == "invalid" else issue,
        "issue_url": _safe_url(doc.github_issue_url),
        "finished": doc.finished,
        "done": done,
        "total": total,
        "held": held,
        "step_count": len(doc.steps),
        "basis": basis,
        "drift": drift,
        "steps": steps,
    }


UPCOMING_MAX = 3


def _upcoming(detail: dict) -> list[dict]:
    """Not-yet-begun steps of an open plan that already has progress (its follow-ups)."""
    if detail["state"] != "open" or not detail["done"] > 0:
        return []
    out: list[dict] = []
    for step in detail["steps"]:
        if step["status_word"] in DONE_WORDS:
            continue
        child = step["child"] if step["child"] and step["child"]["kind"] != "dangling" else None
        child_done = child["done"] if child else 0
        if step["done"] > 0 or child_done > 0:
            continue
        if step["total"] > 0 and step["done"] == step["total"]:
            continue
        out.append(
            {
                "n": step["n"],
                "title": step["title"],
                "status_word": step["status_word"],
                "blurb": step["summary"][0] if step["summary"] else None,
                "child": {"repo": child["repo"], "topic": child["topic"], "done": child["done"], "total": child["total"]}
                if child
                else None,
            }
        )
        if len(out) == UPCOMING_MAX:
            break
    return out


def _summary(detail: dict) -> dict:
    out = {key: value for key, value in detail.items() if key not in ("steps", "about")}
    out["upcoming"] = _upcoming(detail)
    return out


def list_plans(plans_dir: Path, include_completed: bool) -> dict:
    plans_dir = Path(plans_dir)
    root = plans_dir.resolve()
    states = STATES if include_completed else STATES[:1]
    repos = []
    for repo_dir in _children(plans_dir):
        repo = repo_dir.name
        if not _valid_segment(repo) or not _is_dir(repo_dir) or not _inside(repo_dir, root):
            continue
        if not any(_is_dir(repo_dir / state) for state in STATES):
            continue
        plans = []
        for state in states:
            state_dir = repo_dir / state
            if not _is_dir(state_dir) or not _inside(state_dir, root):
                continue
            for topic_dir in _children(state_dir):
                topic = topic_dir.name
                if not _valid_segment(topic) or not _is_dir(topic_dir) or not _inside(topic_dir, root):
                    continue
                for path in _topic_plans(topic_dir, root):
                    detail = _build(path, root, repo, state, topic, path.stem)
                    if detail is not None:
                        plans.append(_summary(detail))
        repos.append({"repo": repo, "plans": plans})
    return {"repos": repos}


def plan_detail(plans_dir: Path, repo: str, state: str, topic: str, name: str) -> dict | None:
    for segment in (repo, topic, name):
        if not _valid_segment(segment):
            raise ValueError(f"bad path segment: {segment!r}")
    if state not in STATES:
        raise ValueError(f"bad state: {state!r}")
    root = Path(plans_dir).resolve()
    path = Path(plans_dir) / repo / state / topic / f"{name}.md"
    if not _is_file(path) or not _inside(path, root):
        return None
    return _build(path, root, repo, state, topic, name)


# ---------------------------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------------------------

PAGE_PATH = Path(__file__).resolve().with_name("plans_ui.html")
DEFAULT_PORT = 8801
DEFAULT_BIND = "127.0.0.1"
PLANS_DIR_ENV = "PLANS_UI_PLANS_DIR"
DETAIL_PREFIX = "/api/plan/"
DETAIL_SEGMENTS = 4
ALLOWED_METHODS = ("GET", "HEAD")
JSON_TYPE = "application/json; charset=utf-8"
HTML_TYPE = "text/html; charset=utf-8"
PAGE_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'"
)


class Response(NamedTuple):
    status: int
    headers: dict
    body: bytes


def _headers(content_type: str | None, csp: bool = False) -> dict:
    headers = {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }
    if content_type:
        headers["Content-Type"] = content_type
    if csp:
        headers["Content-Security-Policy"] = PAGE_CSP
    return headers


def error_response(status: int, message: str, extra: dict | None = None) -> Response:
    headers = _headers(JSON_TYPE)
    headers.update(extra or {})
    return Response(status, headers, json.dumps({"error": message}).encode())


def _json_response(payload: object) -> Response:
    return Response(200, _headers(JSON_TYPE), json.dumps(payload).encode())


def load_page(page_path: Path) -> bytes | None:
    try:
        return Path(page_path).read_bytes()
    except OSError:
        return None


def host_allowed(host: str | None, allowed: frozenset) -> bool:
    """Case-insensitive exact match against the allowlist; a missing Host is never allowed."""
    return host is not None and host.strip().lower() in allowed


def _detail_response(plans_dir: Path, rest: str) -> Response:
    # Split on the raw "/" first, then decode each segment once: an encoded %2f can never add a segment,
    # and NUL, backslash, ".." and empty segments all fail the charset check.
    raw_segments = rest.split("/")
    if len(raw_segments) != DETAIL_SEGMENTS:
        return error_response(400, "bad path")
    try:
        segments = [unquote(s, errors="strict") for s in raw_segments]
    except UnicodeDecodeError:
        return error_response(400, "bad path")
    if not all(_valid_segment(s) for s in segments):
        return error_response(400, "bad path")
    repo, state, topic, name = segments
    if state not in STATES:
        return error_response(400, "bad state")
    detail = plan_detail(plans_dir, repo, state, topic, name)  # segments and state were validated above
    if detail is None:
        return error_response(404, "plan not found")
    return _json_response(detail)


def handle_get(plans_dir: Path, page_path: Path, target: str) -> Response:
    """Route a GET/HEAD request target (Host and method checks already passed)."""
    path, _, query = target.partition("?")
    try:
        if path == "/":
            page = load_page(page_path)
            if page is None:
                return error_response(404, "page not found")
            return Response(200, _headers(HTML_TYPE, csp=True), page)
        if path == "/favicon.ico":
            return Response(204, _headers(None), b"")
        if path == "/api/plans":
            include_completed = parse_qs(query).get("completed") == ["1"]
            return _json_response(list_plans(plans_dir, include_completed))
        if path.startswith(DETAIL_PREFIX):
            return _detail_response(plans_dir, path[len(DETAIL_PREFIX) :])
    except Exception:  # noqa: BLE001 - never leak a traceback to the client
        return error_response(500, "internal error")
    return error_response(404, "not found")


class PlansHandler(BaseHTTPRequestHandler):
    server_version = "plans-ui"
    sys_version = ""
    timeout = 15  # idle or slow clients must not hold a thread forever

    def version_string(self) -> str:
        return self.server_version

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        if getattr(self.server, "verbose", False):
            super().log_message(format, *args)

    def _send(self, resp: Response) -> None:
        if self.request_version == "HTTP/0.9":  # unparsed or ancient request: still send a status line and headers
            self.request_version = "HTTP/1.0"
        self.send_response(resp.status)
        for key, value in resp.headers.items():
            self.send_header(key, value)
        if resp.status != 204:
            self.send_header("Content-Length", str(len(resp.body)))
        self.end_headers()
        if self.command != "HEAD" and resp.status != 204:
            self.wfile.write(resp.body)

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """Protocol-level errors keep the JSON contract; never reflect client input."""
        try:
            short = HTTPStatus(code).phrase.lower()
        except ValueError:
            short = "error"
        self.close_connection = True
        self._send(error_response(code, short))

    def _serve(self) -> None:
        hosts = self.headers.get_all("Host") or []
        if len(hosts) != 1 or not host_allowed(hosts[0], self.server.allowed_hosts):
            self._send(error_response(403, "host not allowed"))
        elif self.command not in ALLOWED_METHODS:
            self._send(error_response(405, "method not allowed", {"Allow": ", ".join(ALLOWED_METHODS)}))
        else:
            self._send(handle_get(self.server.plans_dir, self.server.page_path, self.path))

    do_GET = _serve
    do_HEAD = _serve

    def __getattr__(self, name: str):
        # Any other verb (POST, TRACE, CONNECT, ...) must reach the Host-then-405 path, not the stock 501.
        if name.startswith("do_"):
            return self._serve
        raise AttributeError(name)


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class _Server(ThreadingHTTPServer):
    daemon_threads = True


class _Server6(_Server):
    address_family = socket.AF_INET6


def make_server(
    plans_dir: Path,
    host: str = DEFAULT_BIND,
    port: int = 0,
    allow_hosts: tuple = (),
    *,
    allow_nonloopback: bool = False,
    verbose: bool = False,
    page_path: Path = PAGE_PATH,
) -> ThreadingHTTPServer:
    if not allow_nonloopback and not is_loopback(host):
        raise ValueError(f"refusing to bind non-loopback address {host!r} (pass --allow-nonloopback)")
    server = (_Server6 if ":" in host else _Server)((host, port), PlansHandler)
    actual = server.server_address[1]
    server.plans_dir = Path(plans_dir)
    server.page_path = Path(page_path)
    server.verbose = verbose
    server.allowed_hosts = frozenset(
        [f"127.0.0.1:{actual}", f"localhost:{actual}"] + [h.strip().lower() for h in allow_hosts if h.strip()]
    )
    return server


def default_plans_dir() -> Path:
    env = os.environ.get(PLANS_DIR_ENV)
    return Path(env) if env else Path(__file__).resolve().parents[3] / "plans"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only plans dashboard server")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--bind", default=DEFAULT_BIND)
    parser.add_argument("--plans-dir", type=Path, default=None)
    parser.add_argument("--allow-host", action="append", default=[], help="extra allowed Host value")
    parser.add_argument("--allow-nonloopback", action="store_true", help="permit a non-loopback --bind")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    plans_dir = args.plans_dir or default_plans_dir()
    try:
        server = make_server(
            plans_dir,
            args.bind,
            args.port,
            tuple(args.allow_host),
            allow_nonloopback=args.allow_nonloopback,
            verbose=args.verbose,
        )
    except ValueError as exc:
        print(f"plans-ui: {exc}", file=sys.stderr)
        return 2
    except (OSError, OverflowError) as exc:
        print(f"plans-ui: cannot bind {args.bind}:{args.port}: {exc}", file=sys.stderr)
        return 1
    host, port = server.server_address[0], server.server_address[1]
    shown = f"[{host}]" if ":" in host else host
    print(f"plans-ui: listening on http://{shown}:{port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
