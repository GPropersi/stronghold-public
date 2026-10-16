#!/usr/bin/env python3
"""Core of plan-folder.sh: pick the central-store plan folder for a pushed branch (stdlib only).

    plan_folder.py --stronghold <root> --bucket <bucket> --branch <branch>

Scoring is the deterministic algorithm of the retired /git-push resolver prompt:
  * branch tokens: split on `/ - _`, lowercase, drop the generic prefix tokens, de-duplicate;
  * folder tokens: split on `- _`, lowercase, a trailing `-master` ignored, de-duplicated;
  * +1.0 per shared token of length >= 3, +0.5 per shorter one, +0.5 when a multi-token folder
    name is a contiguous substring of the branch slug (lowercased, `/` -> `-`);
  * match = a unique top score >= 1.0 (an `open/` folder beats `completed/` on a tie, a tie
    inside one state is `ambiguous`), no_match = top score < 1.0.
Documented additions to the prompt: a trailing `-master` on a folder name is ignored for scoring,
and several topics declaring the same `branch:` in one state are `ambiguous`.
Added pre-signal: a topic whose plan frontmatter `branch:` equals the pushed branch is an exact
`match` before any scoring (`open/` first, then `completed/`, alphabetical).
Prints one JSON object {decision, branch, meaningful_tokens, plan_dir (match only), candidates,
suggested_new_folder}; paths are absolute under <root>/plans/<bucket>/.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB_DIR))

import wf_frontmatter  # noqa: E402

PREFIX_TOKENS = frozenset(
    "feature feat fix bugfix refactor chore tests test ts review wip hotfix docs".split()
)
STATES = ("open", "completed")
MAX_NEW_TOKENS = 4
SUMMARY_MAX = 100


def split_tokens(text: str, separators: str) -> list[str]:
    """Lowercased, de-duplicated (first occurrence wins) tokens of `text`."""
    seen: dict[str, None] = {}
    for token in re.split("[" + re.escape(separators) + "]", text.lower()):
        if token:
            seen.setdefault(token, None)
    return list(seen)


def meaningful_tokens(branch: str) -> list[str]:
    return [t for t in split_tokens(branch, "/-_") if t not in PREFIX_TOKENS]


def folder_name_for_scoring(name: str) -> str:
    lowered = name.lower()
    return lowered[: -len("-master")] if lowered.endswith("-master") else lowered


def score_folder(name: str, tokens: list[str], branch_slug: str) -> float:
    folder = folder_name_for_scoring(name)
    folder_tokens = set(split_tokens(folder, "-_"))
    score = 0.0
    for token in tokens:
        if token in folder_tokens:
            score += 1.0 if len(token) >= 3 else 0.5
    # The bonus needs a multi-token folder name: a single-token name inside the branch is already
    # paid for by its token match (the prompt's worked example scores `urls` 1.0, not 1.5).
    if len(folder_tokens) >= 2 and folder in branch_slug:
        score += 0.5
    return score


def suggest_new_folder(branch: str, tokens: list[str]) -> str:
    if tokens:
        return "-".join(tokens[:MAX_NEW_TOKENS])
    return branch.lower().rsplit("/", 1)[-1]


def plan_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.glob("*.md") if p.is_file())


def longest_common_substring(a: str, b: str) -> int:
    """Length of the longest common substring of a and b (small inputs, plain DP)."""
    best = 0
    previous = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        current = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                current[j] = previous[j - 1] + 1
                best = max(best, current[j])
        previous = current
    return best


def summary_of(folder: Path, branch_slug: str) -> str | None:
    files = plan_files(folder)
    if not files:
        return None
    chosen = max(files, key=lambda p: (longest_common_substring(p.stem.lower(), branch_slug), -files.index(p)))
    try:
        lines = chosen.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                lines = lines[index + 1 :]
                break
    head = [line.strip() for line in lines[:5] if line.strip()]
    pick = next((line for line in head if line.startswith("#")), head[0] if head else "")
    pick = pick.lstrip("#").strip()
    return pick[:SUMMARY_MAX] if pick else None


def frontmatter_branch(path: Path) -> str | None:
    try:
        return wf_frontmatter.parse(path).get("branch") or None
    except (OSError, UnicodeDecodeError):
        return None


def enumerate_folders(store: Path) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    for state in STATES:
        base = store / state
        if base.is_dir():
            found.extend((state, p) for p in sorted(base.iterdir()) if p.is_dir())
    return found


def candidate(state: str, folder: Path, score: float, branch_slug: str) -> dict:
    return {"folder": str(folder), "score": score, "summary": summary_of(folder, branch_slug)}


def resolve(stronghold: Path, bucket: str, branch: str) -> dict:
    store = stronghold / "plans" / bucket
    tokens = meaningful_tokens(branch)
    branch_slug = branch.lower().replace("/", "-")
    result: dict = {
        "decision": "no_match",
        "branch": branch,
        "meaningful_tokens": tokens,
        "candidates": [],
        "suggested_new_folder": suggest_new_folder(branch, tokens),
    }
    folders = enumerate_folders(store)
    if not folders:
        return result

    scored = [
        (state, folder, score_folder(folder.name, tokens, branch_slug)) for state, folder in folders
    ]

    declared = [
        t for t in scored if any(frontmatter_branch(md) == branch for md in plan_files(t[1]))
    ]
    open_declared = [t for t in declared if t[0] == "open"]
    pool = open_declared or declared
    if len(pool) == 1:
        state, folder, score = pool[0]
        result.update(
            decision="match",
            plan_dir=str(folder),
            candidates=[candidate(state, folder, score, branch_slug)],
        )
        return result
    if len(pool) > 1:
        result.update(
            decision="ambiguous",
            candidates=[candidate(s, f, sc, branch_slug) for s, f, sc in pool[:3]],
        )
        return result

    state_rank = {state: index for index, state in enumerate(STATES)}
    ranked = sorted(scored, key=lambda t: (-t[2], state_rank[t[0]], t[1].name))
    top = ranked[0][2]
    if top >= 1.0:
        tied = [t for t in ranked if t[2] == top]
        open_tied = [t for t in tied if t[0] == "open"]
        pool = open_tied or tied
        if len(pool) == 1:
            state, folder, score = pool[0]
            result.update(
                decision="match",
                plan_dir=str(folder),
                candidates=[candidate(state, folder, score, branch_slug)],
            )
            return result
        result["decision"] = "ambiguous"
    if top > 0.0:
        shown = ranked[:3]
    else:
        shown = sorted(scored, key=lambda t: (t[1].name, state_rank[t[0]]))[:3]
    result["candidates"] = [candidate(s, f, sc, branch_slug) for s, f, sc in shown]
    return result


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stronghold", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--branch", required=True)
    args = parser.parse_args(argv)
    print(json.dumps(resolve(Path(args.stronghold), args.bucket, args.branch), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
