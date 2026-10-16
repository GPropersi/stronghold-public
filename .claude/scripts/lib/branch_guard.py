#!/usr/bin/env python3
"""Classify a Bash command for the branch-switching hook (block-branch-switching.sh).

Usage: branch_guard.py <payload-cwd> <command>

Prints one JSON object {"items": [...]}; each item is a git invocation that is NOT plainly allowed:

  {"dir": <checkout dir to judge>, "form": "deny|default-ok|create-ahead",
   "name": <branch name or null>, "hint": <the offending git form, for the message>}

  deny          always refused (outside the stronghold): creating or detaching forms
  default-ok    `git checkout|switch <name>`: allowed iff <name> is the repo's default branch
                (gmas / squash-merge cleanup); the hook reads the default branch from the seam
  create-ahead  `git checkout -b <name>`: allowed ONLY when the current branch is the default
                branch and it is ahead of origin/<default> (the confirm-gated /git-push exception)

Allowed forms produce no item. The stronghold exemption, the default-branch seam lookup and the
rev-list count need git/seam access in the target checkout and are done by the hook, not here.

`git -C <dir>` only SELECTS the checkout to judge (the same token walk as push_gate_cmd.py); the
same rules are then applied to that target. A git token must be in command position (only
VAR=value, wrapper words and flags before it), so `echo "git checkout -b x"` is not a command.
`bash -c '<string>'` strings are judged recursively. An unparsable command yields no items (the
hook fails open).
"""

from __future__ import annotations

import json
import os
import sys

from push_gate_cmd import (  # same lib dir
    OPERAND_FLAGS,
    PREFIX_COMMANDS,
    expand_home,
    shell_command_string,
    strip_redirects,
    tokenize,
)

WRAPPERS = PREFIX_COMMANDS | {"if", "elif", "while", "until"}
SUBCOMMANDS = {"checkout", "switch", "branch", "worktree"}

# `git branch` flags that put it in list/delete/move/upstream mode (never creates a ref).
BRANCH_MODE_LONG = {"show-current", "list", "all", "remotes", "contains", "no-contains", "merged",
                    "no-merged", "points-at", "delete", "move", "set-upstream-to",
                    "unset-upstream", "edit-description"}
BRANCH_MODE_SHORT = set("larrdDmMu")
BRANCH_VALUE_LONG = {"sort", "format", "abbrev", "color", "column"}
BRANCH_FORCE_LONG = {"force", "copy"}
BACKUP_PREFIX = "backup/"


def abbrev(option: str, full: str) -> bool:
    """git accepts any unique prefix of a long option; `--de` means `--detach`."""
    return len(option) >= 2 and full.startswith(option)


def only_wrappers(tokens: list[str]) -> bool:
    """True when every token before a git word is a wrapper (so the git word is a command)."""
    skip_operand = False
    for token in tokens:
        if skip_operand:
            skip_operand = False
            continue
        is_assignment = "=" in token and token.split("=", 1)[0].replace("_", "a").isalnum()
        if token in OPERAND_FLAGS:
            skip_operand = True
        elif not (is_assignment or token.startswith("-") or token.isdigit()
                  or os.path.basename(token) in WRAPPERS):
            return False
    return True


def repo_dir_of(path: str, base: str) -> str:
    """The checkout a --git-dir / --work-tree value selects (a .git dir maps to its parent)."""
    if not path:
        return base
    full = path if path.startswith("/") else base + "/" + path
    return os.path.dirname(full.rstrip("/")) if os.path.basename(full.rstrip("/")) == ".git" else full


def walk_to_subcommand(tokens: list[str], git_index: int, cwd: str) -> tuple[str, int] | None:
    """(checkout dir, index of the subcommand) after the global flags following `git`."""
    walk_cwd = cwd
    index = git_index + 1
    while index < len(tokens):
        token = tokens[index]
        if token == "-C":
            if index + 1 >= len(tokens):
                return None
            directory = expand_home(tokens[index + 1])
            if not directory:
                return None
            walk_cwd = directory if directory.startswith("/") else walk_cwd + "/" + directory
            index += 2
        elif token in ("--git-dir", "--work-tree") and index + 1 < len(tokens):
            walk_cwd = repo_dir_of(expand_home(tokens[index + 1]), walk_cwd)
            index += 2
        elif token.startswith(("--git-dir=", "--work-tree=")):
            walk_cwd = repo_dir_of(expand_home(token.partition("=")[2]), walk_cwd)
            index += 1
        elif token in ("-c", "--namespace", "--exec-path"):
            index += 2
        elif token.startswith("-"):
            index += 1
        else:
            return walk_cwd, index
    return None


def creating_checkout_like(args: list[str], sub: str) -> dict:
    """Scan checkout/switch arguments (those before a `--`)."""
    create_letters = set("bB") if sub == "checkout" else set("cC")
    detach_letters = set() if sub == "checkout" else set("d")
    scan = {"create": False, "exception": False, "detach": False, "patch": False,
            "name": None, "positionals": []}
    count = len(args)
    index = 0
    while index < count:
        token = args[index]
        if token == "-":
            scan["positionals"].append(token)
        elif token.startswith("--"):
            option, eq, value = token[2:].partition("=")
            if abbrev(option, "orphan") or (sub == "switch" and (
                    abbrev(option, "create") or abbrev(option, "force-create"))):
                scan["create"] = True
                scan["name"] = value or (args[index + 1] if index + 1 < count else None)
                index += 0 if (eq or value) else 1
            elif abbrev(option, "detach"):
                scan["detach"] = True
            elif abbrev(option, "patch"):
                scan["patch"] = True
            elif option == "conflict" and not eq:
                index += 1
        elif token.startswith("-") and len(token) > 1:
            letters = token[1:]
            hit = [pos for pos, char in enumerate(letters) if char in create_letters]
            if hit:
                scan["create"] = True
                rest = letters[hit[-1] + 1:]
                scan["name"] = rest or (args[index + 1] if index + 1 < count else None)
                if not rest:
                    index += 1
                if sub == "checkout" and "B" not in letters:
                    scan["exception"] = True
            if set(letters) & detach_letters:
                scan["detach"] = True
            if "p" in letters:
                scan["patch"] = True
        else:
            scan["positionals"].append(token)
        index += 1
    return scan


def judge_checkout_like(sub: str, args: list[str]) -> dict | None:
    args = strip_redirects(args)
    has_separator = "--" in args
    before = args[:args.index("--")] if has_separator else args
    scan = creating_checkout_like(before, sub)
    positionals = scan["positionals"]
    name = scan["name"]
    if scan["create"]:
        hint = f"git {sub} " + ("-b" if sub == "checkout" else "-c") + (f" {name}" if name else "")
        form = "create-ahead" if scan["exception"] else "deny"
        return {"form": form, "name": name, "hint": hint}
    if scan["detach"]:
        return {"form": "deny", "name": None, "hint": f"git {sub} --detach"}
    after_separator = args[args.index("--") + 1:] if has_separator else []
    if scan["patch"] or not positionals or (has_separator and after_separator):
        return None
    if positionals == ["HEAD"]:
        return None  # a no-op
    if sub == "checkout" and positionals == ["."]:
        return None
    if len(positionals) == 1 and positionals[0] != "-":
        return {"form": "default-ok", "name": positionals[0],
                "hint": f"git {sub} {positionals[0]}"}
    shown = " ".join(positionals)
    return {"form": "deny", "name": None, "hint": f"git {sub} {shown}"}


def judge_branch(args: list[str]) -> dict | None:
    args = strip_redirects(args)
    positionals: list[str] = []
    mode = False
    forced = False
    skip_value = False
    for token in args:
        if skip_value:
            skip_value = False
            continue
        if token.startswith("--"):
            option = token[2:].partition("=")[0]
            if "=" not in token and option in BRANCH_VALUE_LONG:
                skip_value = True
            if option in BRANCH_MODE_LONG or any(abbrev(option, full) and len(option) >= 3
                                                 for full in BRANCH_MODE_LONG):
                mode = True
            if option in BRANCH_FORCE_LONG or abbrev(option, "force") or abbrev(option, "copy"):
                forced = True
        elif token.startswith("-") and len(token) > 1:
            letters = token[1:]
            if set(letters) & BRANCH_MODE_SHORT:
                mode = True
            if "f" in letters or "c" in letters or "C" in letters:
                forced = True
        else:
            positionals.append(token)
    if forced:
        return {"form": "deny", "name": positionals[0] if positionals else None,
                "hint": "git branch -f/-c (force-moves or copies a ref)"}
    if mode or not positionals:
        return None
    first = positionals[0]
    if first.startswith(BACKUP_PREFIX) and len(first) > len(BACKUP_PREFIX) and len(positionals) <= 2:
        return None
    return {"form": "deny", "name": first, "hint": f"git branch {' '.join(positionals)}"}


def judge_worktree(args: list[str]) -> dict | None:
    args = strip_redirects(args)
    positionals = [token for token in args if not token.startswith("-")]
    if not positionals or positionals[0] != "add":
        return None
    name = None
    for index, token in enumerate(args):
        if token in ("-b", "-B") and index + 1 < len(args):
            name = args[index + 1]
    return {"form": "deny", "name": name, "hint": "git worktree add"}


def judge_subcommand(sub: str, args: list[str]) -> dict | None:
    if sub in ("checkout", "switch"):
        return judge_checkout_like(sub, args)
    if sub == "branch":
        return judge_branch(args)
    return judge_worktree(args)


def analyse_items(cwd: str, command: str, depth: int = 0) -> list[dict]:
    items: list[dict] = []
    for tokens in tokenize(command):
        nested = shell_command_string(tokens) if depth < 3 else None
        if nested is None and depth < 3 and tokens[0] == "eval" and len(tokens) > 1:
            nested = " ".join(tokens[1:])
        if nested is not None:
            items.extend(analyse_items(cwd, nested, depth + 1))
            continue
        for git_index, token in enumerate(tokens):
            if os.path.basename(token) != "git" or not only_wrappers(tokens[:git_index]):
                continue
            walked = walk_to_subcommand(tokens, git_index, cwd)
            if walked is None or tokens[walked[1]] not in SUBCOMMANDS:
                continue
            directory, sub_index = walked
            if not os.path.isdir(directory):
                directory = cwd
            judged = judge_subcommand(tokens[sub_index], tokens[sub_index + 1:])
            if judged is not None:
                items.append({"dir": directory, **judged})
            break
    return items


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    print(json.dumps({"items": analyse_items(argv[1], argv[2])}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
