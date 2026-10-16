#!/usr/bin/env python3
"""Parse a Bash command for the push-gate enforcement hook (require-push-gate.sh).

Usage: push_gate_cmd.py <payload-cwd> <command>

Prints one JSON object: {"kind": "none|script|gitpush", "dir": <checkout dir to judge>,
"branch": <pushed branch or null>, "allow": <bool>, "deny": <reason or null>}.

  kind=script    a `gh-app-push.sh [<branch>]` invocation (any spelling; interpreter / env /
                 VAR=value prefixes are ignored). The dir is the payload cwd.
  kind=gitpush   a `git [-C d] push ...` invocation; the dir is the python port of the token walk
                 in block-unsafe-push.sh (lines 90-153): a git token is a bare `git` or a path
                 ending /git that is not the value of a preceding flag; `-C <dir>` replaces on an
                 absolute path and joins on a relative one; ~, $HOME and ${HOME} are expanded;
                 -c / --git-dir / --work-tree (and their =value forms) are skipped; any other
                 token means "not a clean walk" and the payload cwd is used.
  kind=none      anything else (including an unparsable command: the hook fails open).

The pushed ref is judged fail-closed: only none (the current branch), `feat`, `+feat` and
`feat:feat` shapes naming an existing local branch are accepted; everything else carries a
`deny` reason. `allow` is true for the default-branch carve-out (main/master), which the push
script / block-unsafe-push.sh refuse on their own.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys

SCRIPT_NAME = "gh-app-push.sh"
BENIGN_FLAGS = {"-u", "--set-upstream", "-q", "--quiet", "-v", "--verbose",
                "--no-verify", "--progress", "--no-thin"}
MULTI_REF_FLAGS = {"--all", "--mirror", "--tags", "--delete", "-d", "--follow-tags", "--prune"}
PREFIX_COMMANDS = {"env", "bash", "sh", "zsh", "nohup", "time", "command", "exec", "sudo", "nice",
                   "timeout", "xargs", "then", "do", "else", "!", "{"}
OPERAND_FLAGS = {"-u", "-g", "-U", "-C", "-n", "-s", "-k"}
DEFAULT_BRANCHES = {"main", "master"}
SEPARATOR_CHARS = set(";&|()")

PUSH_VIA_GATE = "push through ~/code/.claude/scripts/gh-app-push.sh with the literal branch name"


def tokenize(command: str) -> list[list[str]]:
    """Split into command segments of shell words.

    A newline is a separator. When shlex cannot parse the text (an unbalanced quote, for example
    inside a heredoc body) a crude quote-blind split is used instead, so the command is still
    judged (fail closed) rather than skipped.
    """
    text = command.replace("\n", " ; ")
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    segments: list[list[str]] = [[]]
    try:
        for token in lexer:
            is_redirect = "<" in token or ">" in token
            if not is_redirect and token and set(token) <= SEPARATOR_CHARS:
                segments.append([])
            else:
                segments[-1].append(token)
    except ValueError:
        segments = [[]]
        for token in text.replace(";", " ; ").replace("(", " ; ").replace(")", " ; ").split():
            if token in (";", "&", "&&", "|", "||"):
                segments.append([])
            else:
                segments[-1].append(token.strip("'\""))
    return [segment for segment in segments if segment]


def strip_redirects(tokens: list[str]) -> list[str]:
    kept: list[str] = []
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if "<" in token or ">" in token:
            if kept and kept[-1] in ("0", "1", "2"):
                kept.pop()
            skip_next = True
            continue
        kept.append(token)
    return kept


def expand_home(directory: str) -> str:
    home = os.environ.get("HOME", "")
    for prefix in ("~", "$HOME", "${HOME}"):
        if directory == prefix or directory.startswith(prefix + "/"):
            return home + directory[len(prefix):]
    return directory


def walk_git(tokens: list[str], push_index: int, git_index: int, cwd: str) -> str | None:
    """Directory after walking the global flags between git and push, None if not clean."""
    walk_cwd = cwd
    index = git_index + 1
    while index < push_index:
        token = tokens[index]
        if token == "-C":
            if index + 1 >= push_index:
                return None
            directory = expand_home(tokens[index + 1])
            if not directory:
                return None
            walk_cwd = directory if directory.startswith("/") else walk_cwd + "/" + directory
            index += 2
        elif token in ("-c", "--git-dir", "--work-tree"):
            index += 2
        elif token.startswith("-"):
            index += 1  # an unknown global flag (--no-pager, -p, ...): still a push, keep walking
        else:
            return None
    return walk_cwd if index == push_index else None


def find_git_push(tokens: list[str], cwd: str) -> tuple[str, int] | None:
    """(dir, push_index) for the last `git ... push` in the segment, else None."""
    for push_index in range(len(tokens) - 1, 0, -1):
        if tokens[push_index] != "push":
            continue
        for git_index in range(push_index - 1, -1, -1):
            previous = tokens[git_index - 1] if git_index > 0 else ""
            if os.path.basename(tokens[git_index]) != "git" or previous in (
                    "-C", "-c", "--git-dir", "--work-tree"):
                continue
            resolved = walk_git(tokens, push_index, git_index, cwd)
            if resolved is not None:
                return resolved, push_index
    return None


def find_script(tokens: list[str]) -> int | None:
    """Index of the gh-app-push.sh token when it is in command position, else None.

    Only wrapper words may precede it: VAR=value assignments, flags (and the operand of the
    short flags that take one), a bare number (timeout 10) and the PREFIX_COMMANDS.
    """
    skip_operand = False
    for index, token in enumerate(tokens):
        if os.path.basename(token) == SCRIPT_NAME:
            return index
        if skip_operand:
            skip_operand = False
            continue
        is_assignment = "=" in token and token.split("=", 1)[0].replace("_", "a").isalnum()
        if token in OPERAND_FLAGS:
            skip_operand = True
        elif not (is_assignment or token.startswith("-") or token.isdigit()
                  or os.path.basename(token) in PREFIX_COMMANDS):
            return None
    return None


def shell_command_string(tokens: list[str]) -> str | None:
    """The command string of `bash -c '<string>'` (also sh/zsh, combined flags like -lc)."""
    if not tokens or os.path.basename(tokens[0]) not in ("bash", "sh", "zsh"):
        return None
    for index, token in enumerate(tokens[1:], start=1):
        if token.startswith("-") and not token.startswith("--") and token.endswith("c"):
            return tokens[index + 1] if index + 1 < len(tokens) else None
    return None


def git_out(directory: str, *args: str) -> str:
    result = subprocess.run(["git", "-C", directory, *args], capture_output=True, text=True,
                            check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def local_branch_exists(directory: str, name: str) -> bool:
    return bool(git_out(directory, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}"))


def judge_name(directory: str, name: str) -> dict:
    """Judge one plain branch-name argument."""
    if not name or any(char in name for char in "$`*?["):
        return {"deny": f"the pushed ref {name!r} is not a literal branch name; {PUSH_VIA_GATE}"}
    if name in DEFAULT_BRANCHES:
        return {"branch": name, "allow": True}
    if not local_branch_exists(directory, name):
        return {"deny": f"{name!r} is not an existing local branch (refspecs, refs/heads/ forms "
                        f"and HEAD cannot be certified by one gate result); {PUSH_VIA_GATE}"}
    return {"branch": name}


def judge_current(directory: str) -> dict:
    current = git_out(directory, "branch", "--show-current")
    if not current:
        return {"deny": f"detached HEAD cannot be certified by a gate result; {PUSH_VIA_GATE}"}
    return judge_name(directory, current)


def judge_script(directory: str, args: list[str]) -> dict:
    args = strip_redirects(args)
    if not args:
        return judge_current(directory)
    if len(args) > 1:
        return {"deny": f"gh-app-push.sh takes exactly one branch name, got {len(args)} "
                        f"arguments; {PUSH_VIA_GATE}"}
    return judge_name(directory, args[0])


def judge_git_push(directory: str, args: list[str]) -> dict:
    args = strip_redirects(args)
    positionals: list[str] = []
    for token in args:
        if token in MULTI_REF_FLAGS:
            return {"deny": f"git push {token} names more than one ref, which a single gate "
                            f"result cannot certify; {PUSH_VIA_GATE}"}
        if token in BENIGN_FLAGS:
            continue
        if token.startswith("-"):
            return {"deny": f"git push option {token!r} is not on the benign allowlist, so the "
                            f"pushed ref cannot be located reliably; {PUSH_VIA_GATE}"}
        positionals.append(token)
    refspecs = positionals[1:]
    if len(refspecs) > 1:
        return {"deny": f"git push with {len(refspecs)} refspecs cannot be certified by one gate "
                        f"result; {PUSH_VIA_GATE}"}
    if not refspecs:
        return judge_current(directory)
    spec = refspecs[0]
    if spec.startswith("+"):
        spec = spec[1:]
    if ":" in spec:
        source, _, destination = spec.partition(":")
        if source != destination:
            return {"deny": f"refspec {refspecs[0]!r} pushes a different source than "
                            f"destination; {PUSH_VIA_GATE}"}
        spec = source
    return judge_name(directory, spec)


def analyse_items(cwd: str, command: str, depth: int = 0) -> list[dict]:
    """One judgment per gated invocation in the command (every segment, `bash -c` recursed)."""
    items: list[dict] = []
    for tokens in tokenize(command):
        nested = shell_command_string(tokens) if depth < 3 else None
        if nested is not None:
            items.extend(analyse_items(cwd, nested, depth + 1))
            continue
        base = {"kind": "none", "dir": cwd, "branch": None, "allow": False, "deny": None}
        script_index = find_script(tokens)
        if script_index is not None:
            items.append({**base, "kind": "script", **judge_script(cwd, tokens[script_index + 1:])})
            continue
        found = find_git_push(tokens, cwd)
        if found is not None:
            directory, push_index = found
            if not os.path.isdir(directory):
                directory = cwd  # an unexpandable -C target: judge the payload cwd's checkout
            judged = judge_git_push(directory, tokens[push_index + 1:])
            items.append({**base, "kind": "gitpush", "dir": directory, **judged})
    return items


def analyse(cwd: str, command: str) -> dict:
    """The first gated invocation (a deny wins) plus every one under `items`."""
    items = analyse_items(cwd, command)
    chosen = next((item for item in items if item["deny"]), items[0] if items else None)
    if chosen is None:
        chosen = {"kind": "none", "dir": cwd, "branch": None, "allow": False, "deny": None}
    return {**chosen, "items": items}


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    print(json.dumps(analyse(argv[1], argv[2])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
