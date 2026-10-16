#!/usr/bin/env python3
"""Core of pr-finish.sh: the label / milestone / board / assignee / reviewer plumbing of /git-push
Step 9 (stdlib only). Every decision comes from gh output and exit codes.

    pr_finish.py --pr N --slug OWNER/REPO --graphql-dir DIR [--labels a,b] [--milestone T]
                 [--board TITLE] [--bot LOGIN] [--reviewer LOGIN]

Env: PF_GH (the gh binary: gh-bot.sh in production, a stub in tests).
Exit: 0 success (logged skips are success) | 3 unknown label/milestone | 4 gh failed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


class Stop(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class Runner:
    def __init__(self, graphql_dir: Path):
        self.gh = os.environ["PF_GH"]
        self.graphql_dir = graphql_dir
        self.requested: set[str] = set()

    def call(self, *args: str, tolerate: bool = False) -> subprocess.CompletedProcess:
        result = subprocess.run([self.gh, *args], text=True, capture_output=True)
        if result.returncode != 0 and not tolerate:
            detail = (result.stderr or result.stdout).strip().splitlines()
            raise Stop(4, f"gh {' '.join(args[:3])} failed: {detail[0] if detail else 'no output'}")
        return result

    def json(self, *args: str, tolerate: bool = False):
        result = self.call(*args, tolerate=tolerate)
        if result.returncode != 0 or not result.stdout.strip():
            return None
        try:
            return json.loads(result.stdout)
        except ValueError:
            if tolerate:
                return None
            raise Stop(4, f"gh {' '.join(args[:3])} returned non-JSON output") from None

    def graphql(self, name: str, *variables: str, tolerate: bool = False):
        path = self.graphql_dir / f"{name}.graphql"
        return self.json("api", "graphql", "-F", f"query=@{path}", *variables, tolerate=tolerate)


def dig(data, *keys):
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def split_csv(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


def validate(run: Runner, slug: str, labels: list[str], milestone: str) -> None:
    if labels:
        listing = run.json("label", "list", "--repo", slug, "--limit", "200", "--json", "name") or []
        valid = [item.get("name") for item in listing if isinstance(item, dict) and item.get("name")]
        unknown = [name for name in labels if name not in valid]
        if unknown:
            raise Stop(3, f"unknown label(s): {', '.join(unknown)}\nvalid labels: {', '.join(valid)}")
    if milestone:
        listing = run.json("api", f"repos/{slug}/milestones?per_page=100") or []
        valid = [item.get("title") for item in listing if isinstance(item, dict) and item.get("title")]
        if milestone not in valid:
            raise Stop(3, f"unknown milestone: {milestone}\nvalid milestones: {', '.join(valid)}")


def apply_labels_and_milestone(run: Runner, pr: str, slug: str, labels: list[str], milestone: str) -> None:
    state = run.json("pr", "view", pr, "--repo", slug, "--json", "labels,milestone,reviewRequests") or {}
    have = {item.get("name") for item in state.get("labels") or [] if isinstance(item, dict)}
    missing = [name for name in labels if name not in have]
    if missing:
        run.call("pr", "edit", pr, "--repo", slug, "--add-label", ",".join(missing))
        print(f"labels: added {', '.join(missing)}")
    elif labels:
        print("labels: already applied")
    if milestone:
        if dig(state, "milestone", "title") == milestone:
            print("milestone: already set")
        else:
            run.call("pr", "edit", pr, "--repo", slug, "--milestone", milestone)
            print(f"milestone: set {milestone}")
    run.requested = {
        str(item.get("login", "")).lower() for item in state.get("reviewRequests") or [] if isinstance(item, dict)
    }


def find_board(run: Runner, owner: str, title: str) -> str:
    for query, path in (
        ("find-project", ("data", "organization", "projectsV2", "nodes")),
        ("find-project-user", ("data", "user", "projectsV2", "nodes")),
    ):
        data = run.graphql(query, "-f", f"login={owner}", tolerate=True)
        for node in dig(data, *path) or []:
            if isinstance(node, dict) and node.get("title") == title and node.get("id"):
                return node["id"]
    raise Stop(4, f"project board not found by exact title: {title}")


def pr_node_id(run: Runner, owner: str, name: str, pr: str) -> str:
    data = run.graphql("get-pr-node-id", "-f", f"owner={owner}", "-f", f"name={name}", "-F", f"number={pr}")
    node = dig(data, "data", "repository", "pullRequest", "id")
    if not node:
        raise Stop(4, f"could not resolve the node id of PR #{pr}")
    return node


def add_to_board(run: Runner, owner: str, name: str, pr: str, board: str, node: str) -> None:
    project = find_board(run, owner, board)
    status = run.graphql("get-status-field", "-f", f"projectId={project}")
    field = dig(status, "data", "node", "field") or {}
    option = next(
        (o.get("id") for o in field.get("options") or [] if isinstance(o, dict) and o.get("name") == "In progress"),
        None,
    )
    if not field.get("id") or not option:
        raise Stop(4, f"board '{board}' has no Status field with an 'In progress' option")
    items = run.graphql(
        "get-pr-project-items", "-f", f"owner={owner}", "-f", f"name={name}", "-F", f"number={pr}"
    )
    existing = [
        n for n in dig(items, "data", "repository", "pullRequest", "projectItems", "nodes") or []
        if isinstance(n, dict) and dig(n, "project", "id") == project
    ]
    if existing:
        print(f"board: PR already on '{board}' (status left untouched)")
        return
    added = run.graphql("add-to-project", "-f", f"projectId={project}", "-f", f"contentId={node}")
    item = dig(added, "data", "addProjectV2ItemById", "item", "id")
    if not item:
        raise Stop(4, "adding the PR to the board returned no item id")
    run.graphql(
        "set-status", "-f", f"projectId={project}", "-f", f"itemId={item}",
        "-f", f"fieldId={field['id']}", "-f", f"optionId={option}",
    )
    print(f"board: added to '{board}' with Status In progress")


def assign_bot(run: Runner, bot: str, node: str) -> None:
    user = run.json("api", f"/users/{bot}", tolerate=True)
    bot_id = dig(user, "node_id")
    if not bot_id:
        found = run.graphql("find-bot", "-f", f"login={bot}", tolerate=True)
        bot_id = dig(found, "data", "user", "id")
    if not bot_id:
        print(f"assignee: skipped (could not resolve a node id for {bot})")
        return
    path = run.graphql_dir / "assign-bot.graphql"
    result = run.call(
        "api", "graphql", "-F", f"query=@{path}", "-f", f"assignableId={node}", "-f", f"assigneeId={bot_id}",
        tolerate=True,
    )
    if result.returncode == 0:
        print(f"assignee: {bot}")
        return
    output = (result.stdout or "") + (result.stderr or "")
    if "FORBIDDEN" in output or "not supported" in output:
        print("assignee: skipped (GitHub refuses bot assignment with an App installation token)")
        return
    raise Stop(4, "assigning the bot failed: " + (output.strip().splitlines() or ["no output"])[0])


def request_reviewer(run: Runner, pr: str, slug: str, reviewer: str) -> None:
    if reviewer.lower() in run.requested:
        print(f"reviewer: {reviewer} already requested")
        return
    run.call("pr", "edit", pr, "--repo", slug, "--add-reviewer", reviewer)
    print(f"reviewer: requested {reviewer}")


def finish(args: argparse.Namespace) -> None:
    run = Runner(Path(args.graphql_dir))
    owner, _, name = args.slug.partition("/")
    labels = split_csv(args.labels)
    validate(run, args.slug, labels, args.milestone)
    apply_labels_and_milestone(run, args.pr, args.slug, labels, args.milestone)
    node = None
    if args.board or args.bot:
        node = pr_node_id(run, owner, name, args.pr)
    if args.board:
        add_to_board(run, owner, name, args.pr, args.board, node)
    else:
        print("board: skipped (no GitHub project board declared)")
    if args.bot:
        assign_bot(run, args.bot, node)
    else:
        print("assignee: skipped (no Bot identity declared)")
    if args.reviewer:
        request_reviewer(run, args.pr, args.slug, args.reviewer)
    else:
        print("reviewer: skipped (PR reviewer is n/a)")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", required=True)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--graphql-dir", required=True)
    parser.add_argument("--labels", default="")
    parser.add_argument("--milestone", default="")
    parser.add_argument("--board", default="")
    parser.add_argument("--bot", default="")
    parser.add_argument("--reviewer", default="")
    args = parser.parse_args(argv)
    try:
        finish(args)
    except OSError as err:
        print(f"pr-finish.sh: cannot run gh: {err}", file=sys.stderr)
        return 4
    except Stop as stop:
        print(f"pr-finish.sh: {stop.message}", file=sys.stderr)
        return stop.code
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
