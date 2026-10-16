"""Tokenise a `make` command for the live-resource guard (block-worktree-live-resources.sh).

CLI: wt_parse_make.py <payload-cwd> <command>   (positional only, so a command starting with `-` is data)

Output: exit 0 and stdout = the resolved absolute directory on line 1, then one make target per
line (`help` when none was given). Any other outcome exits 1 with empty stdout: the caller
fails open. Stdlib only.

Grammar (the guard is an anti-accident control, not a sandbox; unsupported shapes fail open):
  - A raw newline, `$(` or a backtick anywhere, an unterminated quote, or an unquoted `;`, `;;`,
    `&&`, `||`, `(` or `)` operator => reject (those characters inside a quoted or mixed word,
    e.g. `FOO='a;b'` or `-C "/p/(x)"`, are plain text). A word-initial `#` starts a shell comment:
    everything from it on is dropped; a mid-word `#` (`safe#x`) is literal.
  - `|`, `|&` and a lone `&` end the command (what follows is another command).
  - Redirections (`>`, `>>`, `>&`, `&>`, `&>>`, `<`, `<<`, `<<<`, `<&`, `>|`) are skipped together
    with their operand, and with an all-digit fd word right before the operator, wherever they
    sit (before, between or after words). A redirection with no operand => reject. The fd-drop
    heuristic is blind to context, so it also eats a wrapper operand: `timeout 60 >f make up`
    loses the DURATION and fails open.
  - Prefix region: `VAR=val` words and the wrappers time, env, nohup, nice, timeout, command and
    sudo (with their defined operands) are skipped; the command word must have basename `make`
    or `gmake`. An unsupported wrapper flag => reject.
  - make arguments: flags at any position until the first `--`. `-C DIR` / `-CDIR` /
    `--directory DIR` / `--directory=DIR` stack like `git -C`; -f -I -o -W -E (and long forms)
    take an operand; -j/-l (and --jobs/--load-average) take an attached value or a following
    all-digit word; -O/--output-sync take an attached value only; short-flag clusters are walked
    letter by letter; every other flag is valueless. `VAR=val` words are not targets.
"""

from __future__ import annotations

import os
import re
import shlex
import sys

SEPARATORS = {"|", "|&", "&"}
REJECTED_OPERATORS = {"&&", "||"}
REDIRECT_CHARS = set("<>&")
VAR_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
ALL_DIGITS = re.compile(r"^[0-9]+$")

MAKE_NAMES = {"make", "gmake"}
WRAPPERS = {"time", "env", "nohup", "nice", "timeout", "command", "sudo"}
OPERAND_SHORT = set("fIoWE")
# Long-form aliases of the -f -I -o -W -E short flags above (each takes an operand).
OPERAND_LONG = {
    "--file",
    "--makefile",
    "--include-dir",
    "--old-file",
    "--assume-old",
    "--what-if",
    "--new-file",
    "--assume-new",
    "--eval",
}
OPTIONAL_NUMERIC_LONG = {"--jobs", "--load-average"}

SUDO_VALUELESS = set("EHnSbkKP")
SUDO_VALUED = set("ughprtCDRTU")
TIMEOUT_VALUELESS = {"--foreground", "--preserve-status", "-v"}
TIMEOUT_VALUED = {"-k", "--kill-after", "-s", "--signal"}


class Reject(Exception):
    """The command has a shape the guard does not parse: the caller fails open."""


def is_redirect(token: str) -> bool:
    # An empty token (a quoted `''` word) is not a redirect even though all() of nothing is True.
    return token == ">|" or (token != "" and all(char in REDIRECT_CHARS for char in token))


def tokenise(command: str) -> list[str]:
    if "\n" in command or "$(" in command or "`" in command:
        raise Reject
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""  # a mid-word `#` is literal in bash; word-initial `#` is handled below
    try:
        tokens = list(lexer)
    except ValueError as error:
        raise Reject from error
    kept: list[str] = []
    for token in tokens:
        if token.startswith("#"):
            break  # shell comment: the rest of the line is not part of the command
        if token in SEPARATORS:
            break
        # Unquoted `;` `(` `)` arrive as their own punctuation tokens, so a token made solely of
        # those characters is an operator; a mixed token (quoted arg) is a plain word.
        if token in REJECTED_OPERATORS or (token and set(token) <= set(";()")):
            raise Reject
        kept.append(token)
    return kept


def strip_redirections(tokens: list[str]) -> list[str]:
    words: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if is_redirect(token):
            if index + 1 >= len(tokens):
                raise Reject
            if words and ALL_DIGITS.match(words[-1]):
                words.pop()
            index += 2
            continue
        words.append(token)
        index += 1
    return words


def take(words: list[str], index: int) -> str:
    if index >= len(words):
        raise Reject
    return words[index]


def skip_wrapper(words: list[str], index: int) -> int:
    """`words[index]` is a wrapper; return the index just past it and its flags/operands."""
    name = os.path.basename(words[index])
    index += 1
    if name == "time":
        while index < len(words) and words[index].startswith("-"):
            if words[index] != "-p":
                raise Reject
            index += 1
    elif name == "env":
        while index < len(words) and words[index].startswith("-"):
            flag = words[index]
            if flag in ("-i", "-", "-0", "-v"):
                index += 1
            elif flag == "-u":
                take(words, index + 1)
                index += 2
            else:
                raise Reject
    elif name == "nohup":
        if index < len(words) and words[index].startswith("-"):
            raise Reject
    elif name == "nice":
        while index < len(words) and words[index].startswith("-"):
            flag = words[index]
            if flag in ("-n", "--adjustment"):
                take(words, index + 1)
                index += 2
            elif re.match(r"^(-[0-9]+|--adjustment=.+)$", flag):
                index += 1
            else:
                raise Reject
    elif name == "timeout":
        while index < len(words) and words[index].startswith("-"):
            flag = words[index]
            if flag in TIMEOUT_VALUELESS:
                index += 1
            elif flag in TIMEOUT_VALUED:
                take(words, index + 1)
                index += 2
            elif re.match(r"^--(kill-after|signal)=.+$", flag):
                index += 1
            else:
                raise Reject
        take(words, index)  # the DURATION operand
        index += 1
    elif name == "command":
        while index < len(words) and words[index].startswith("-"):
            if words[index] not in ("-p", "--"):
                raise Reject
            index += 1
    elif name == "sudo":
        while index < len(words) and words[index].startswith("-"):
            flag = words[index]
            if flag == "--":
                index += 1
                break
            if len(flag) == 2 and flag[1] in SUDO_VALUELESS:
                index += 1
            elif len(flag) == 2 and flag[1] in SUDO_VALUED:
                take(words, index + 1)
                index += 2
            else:
                raise Reject
    return index


def find_command_word(words: list[str]) -> int:
    index = 0
    while index < len(words):
        word = words[index]
        if VAR_ASSIGNMENT.match(word):
            index += 1
        elif os.path.basename(word) in WRAPPERS:
            index = skip_wrapper(words, index)
        else:
            break
    if index >= len(words) or os.path.basename(words[index]) not in MAKE_NAMES:
        raise Reject
    return index


def expand_home(path: str) -> str:
    for prefix in ("~", "$HOME", "${HOME}"):
        if path == prefix or path.startswith(prefix + "/"):
            home = os.environ.get("HOME", "")
            if not home:
                raise Reject
            return home + path[len(prefix):]
    return path


def apply_directory(current: str, value: str) -> str:
    value = expand_home(value)
    if os.path.isabs(value):
        return value
    return os.path.join(current, value)


def parse_make_arguments(words: list[str], cwd: str) -> tuple[str, list[str]]:
    directory = cwd
    targets: list[str] = []
    after_double_dash = False
    index = 0
    while index < len(words):
        word = words[index]
        index += 1
        if not word:
            continue  # `make ''`: an empty word is not a target
        if after_double_dash or not word.startswith("-") or word == "-":
            if "=" not in word:  # VAR=val is a variable assignment, not a target
                targets.append(word)
            continue
        if word == "--":
            after_double_dash = True
        elif word.startswith("--"):
            name, has_value, value = word.partition("=")
            if name == "--directory":
                if not has_value:
                    value = take(words, index)
                    index += 1
                directory = apply_directory(directory, value)
            elif name in OPERAND_LONG:
                if not has_value:
                    take(words, index)
                    index += 1
            elif name in OPTIONAL_NUMERIC_LONG:
                if not has_value and index < len(words) and ALL_DIGITS.match(words[index]):
                    index += 1
        else:
            for position, letter in enumerate(word[1:], start=1):
                rest = word[position + 1:]
                if letter == "C" or letter in OPERAND_SHORT:
                    if not rest:
                        rest = take(words, index)
                        index += 1
                    if letter == "C":
                        directory = apply_directory(directory, rest)
                    break
                if letter in "jl":
                    if not rest and index < len(words) and ALL_DIGITS.match(words[index]):
                        index += 1
                    break
                if letter == "O":
                    break
    return os.path.normpath(directory), targets or ["help"]


def parse(cwd: str, command: str) -> tuple[str, list[str]]:
    words = strip_redirections(tokenise(command))
    start = find_command_word(words)
    directory, targets = parse_make_arguments(words[start + 1:], cwd)
    if "\n" in directory:
        raise Reject
    return directory, targets


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        return 1
    try:
        directory, targets = parse(argv[1], argv[2])
    except Reject:
        return 1
    sys.stdout.write("\n".join([directory, *targets]) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
