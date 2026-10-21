# Slice Planner — the Skill Step 2 subagent

The intellectual core of `/split-pr`: assign **every** changed file to exactly one part, in a legal
merge order, and justify each assignment. This file holds (0) what the orchestrator must produce as
input, (1) the prompt to hand the subagent, and (2) the output schema the orchestrator parses.

The planner is **read-only over the repo** plus one `Write` call for its output. It never cuts a
branch, never applies a patch, never edits a tracked file. Building is Skill Step 3's job, and only
after the user accepts the split.

---

## 0. Orchestrator: producing the input

Run these from inside the sub-repo checkout (`~/code/<repo-dir>`), substituting the **literal** pinned
`BASE` SHA recorded in Skill Step 1d — never `$BASE`, never a shell variable
(`block-command-substitution.sh`, and Bash state does not persist between calls).

| #   | Command                                                                                   | Produces                                              |
| --- | ----------------------------------------------------------------------------------------- | ----------------------------------------------------- |
| 1   | `git diff --name-status --no-renames BASE...<branch> > <scratch>/name-status.txt` | the authoritative file list with `A`/`M`/`D` status   |
| 2   | `git diff --no-renames BASE...<branch> > <scratch>/net.diff`                      | every file's **net** diff, in one file                |
| 3   | `git diff --stat --no-renames BASE...<branch> > <scratch>/stat.txt`               | per-file line counts, for the AskUserQuestion summary |

- **`BASE...<branch>` (three dots) is the net diff** against the merge base, so "a later commit
  modified an earlier commit's file" is already collapsed — that failure mode does not exist here.
- **`--no-renames` is mandatory.** A one-sided rename in a path-filtered diff produces a patch that
  cannot apply; suppressing rename detection turns it into a plain delete + add.
- **Dump to `/tmp/claude/`, not the session scratchpad.** `allow-tmp-access.sh` pre-approves any Bash
  command whose text matches `/tmp/claude/` (or `$TMPDIR/`, `/var/folders/*/T/`,
  `plans/<topic>/tmp/`); the per-session scratchpad path is **not** in that regex, so a redirect
  there can prompt. `Bash(git diff:*)` matches regardless of the redirect target.
- If step 2's output is large, split it per file (`git diff --no-renames BASE...<branch> -- <path>`)
  so the planner can read one file's diff at a time instead of the whole blob.

Hand the planner these paths plus:

- the **plan file** for `<branch>` and its per-step file lists (for Rule 6; if the branch has no plan
  in the central store, say so — then every part's `plan-steps` is `none`);
- the working repo's **`## Claude Config`** section (the **Stack** key selects which atoms apply);
- `~/code/.claude/skills/split-pr/references/ci-gates.md` — the per-repo "what fails on a partial
  change" catalog.

---

## 1. The prompt

> You are the **slice planner** for `/split-pr`. You are given the complete net diff of a branch
> whose CI is **already green**, and you must partition its changed files into N parts that merge
> **sequentially off the default branch**, such that **every prefix of the order is independently
> green**. You do not build anything and you do not edit any tracked file: you produce one analysis
> file and stop.
>
> Inputs: `<name-status.txt>`, `<net.diff>`, `<stat.txt>`, the plan file `<plan-path>`, the repo's
> `## Claude Config`, and `references/ci-gates.md`. Read the repo's `CLAUDE.md` for its generator,
> registry and bridge conventions rather than assuming any stack's rules.
>
> Apply the six rules below **in order**. Every file in `name-status.txt` must appear in exactly one
> part — no file omitted, no file in two parts (the only exception is a declared hunk-split seam,
> Rule 3c, which must be flagged explicitly).

### Rule 1 — assign by generator ownership, not directory

A file that a generator produces belongs to the part containing that generator's **input**,
wherever the file lives on disk. Directory-based seam detection is wrong, and these two
counter-examples are why — both are real assignments from PR 749:

| File                                                        | Naive assignment | Correct assignment | Why                                                                                                                                                                                  |
| ----------------------------------------------------------- | ---------------- | ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `frontend/types/api.d.ts`, `frontend/types/openapi.json`    | frontend         | **backend**        | `types-staleness.yml` regenerates both from backend sources (`flask openapi generate` → `openapi-typescript`) and hard-fails on any drift via `git diff --exit-code frontend/types/` |
| `backend/utils/constants.py`                                | backend          | **frontend**       | here all 43 changed lines are the `MEMBER_ADD_*` string bridge — the file's diff is purely a frontend artifact, so it ships with the frontend part                                   |

The test is **what regenerates the file**, never where it sits. A frontend part that tried to rescue
itself by *carrying* the generated types fails anyway: the staleness job regenerates them from the
**unchanged** backend and diffs, so the committed types are stale by construction. There is no
workaround — the generator's input must merge first.

`frontend/types/metrics-flows.ts` is the same shape: `event-coverage-staleness.yml` regenerates it
from `backend/metrics/` and diffs it, so it is a **backend** artifact sitting in `frontend/types/`.
(It is absent from PR 749's diff — a DOMAIN-only event changes no flow, so the generator reproduces
it byte-identically. Absence from a diff is not evidence against the rule.)

**`frontend/types/` is a *mixed* directory — do not read Rule 1 as "everything under it is a backend
artifact."** The staleness job's `git diff --exit-code frontend/types/` covers the whole directory,
but only the *generated* files in it are backend-owned. `api.d.ts`, `openapi.json` and
`metrics-flows.ts` are generator output; `member.ts` and `api-helpers.d.ts` are hand-written and
belong to whichever part edits them (in PR 749, the frontend part). The test is still **what
regenerates this file**, file by file — never the directory, and never the job's path filter.

### Rule 2 — never cut an atom

A set of files whose consistency a CI job checks is an **atom**: splitting it across parts makes the
earlier part red even though the end state is green. Each atom below names the job that enforces it.

**The table is the `flask-jinja-vanillajs-vite` (app-main) instance of a general rule** — read the
working repo's **Stack** seam key and take its own atoms from `references/ci-gates.md`. Either way,
**verify every enforcer against the repo's own `.github/workflows/` before relying on it**: workflow
layouts move, and a stale enforcer name sends a debugging user to the wrong job (that is exactly how
the `tsc` row below was wrong for three review passes).

**Verify them at the branch, not at `origin/<default-branch>`.** The invariant the split must
preserve is "each part is green **under the CI that will run on it**", and a part PR is cut from the
*then-current* default branch — so for a normal split (a branch whose `BASE` is days old) the
branch's workflows and the default branch's workflows are the same set and the distinction is moot.
It stops being moot the moment `BASE` is **stale**, and then reading `origin/<default-branch>` invents
gates that the diff never had to satisfy and hides ones it did. Read both and diff them:

```
git ls-tree -r --name-only BASE -- .github/workflows/
git ls-tree -r --name-only origin/<default-branch> -- .github/workflows/
```

Worked case, verified: PR 749's `BASE` (`cef3677c`, 2026-08-24) has **no**
`.github/workflows/registry-staleness.yml`, no `backend/endpoint_registry/` package, no
`docs/endpoints/endpoint-registry.json` and no `make generate-endpoints` — the endpoint registry was
then a single **hand-maintained `ENDPOINT_REGISTRY.md` at the repo root**. All of that arrived later.
So the `Endpoint Registry Audit` row below, and Rule 3b's generator-driven amendment, are correct for
a split taken **today** and simply do not apply to that historical diff. If a gate in this table does
not exist at `BASE`, say so in §2.6 and drop it for that split — do not synthesize a file the diff
never contained to satisfy it.

| Atom                                                                                                                                        | Enforced by                                                                                                                                                                                                               |
| ------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Backend route/schema/decorator change + `frontend/types/openapi.json` + `api.d.ts`                                                          | `types-staleness.yml` (CI job `Generated Types Freshness`) — regenerates, prettier-formats, then `git diff --exit-code frontend/types/`                                                                                   |
| Metrics event: `events.py` + `event_registry.py` + `dimension_models.py` + `resources.py` + `metrics-*.ts` + the `record_event()` call site | `event-coverage-staleness.yml` (`Event Coverage Audit`) — `flask metrics audit --strict`, **bidirectional**, so neither half is safe alone; it also regenerates and diffs `frontend/types/metrics-flows.ts`               |
| Route + its `all_routes.py` JS linkage + the generated `docs/endpoints/endpoint-registry.json` **and** `ENDPOINT_REGISTRY.md` pair          | `registry-staleness.yml` (`Endpoint Registry Audit`) — `flask endpoints audit --strict`; see Rule 3b, the `.md` must be the byte-exact render of the committed `.json`                                                    |
| TS-read string: `<domain>_strs.py` + `STRINGS` + `generate_strings_js()` + `test-setup.ts` mock + the TS consumer                           | nothing in CI — vitest reads `undefined` and still passes, so only a **built UI** run catches it (Skill Step 4)                                                                                                           |
| Every `.ts` in one import cycle                                                                                                             | `make typecheck` (`tsc --noEmit` over the app, test and node tsconfigs) — a **step of the `Code Quality` job**, which every other CI job `needs:`, so a cut atom fails the pipeline at the gate rather than in a test leg |
| Migration + its model change                                                                                                                | convention; must land in the **earliest** part needing it                                                                                                                                                                 |
| Shared test helper (`tests/functional/playwright_utils.py` and its per-area twins, `db_utils.py`, `locators.py`, `conftest.py`, `tests/integration/utils.py`) + every marker leg using it | the integration (10 legs) and UI (13 legs) matrices in `test.yml`                                                                                                                                                         |

**Correction worth carrying:** `tsc --noEmit` does **not** run inside the 13 `Tests-UI` legs. Those
legs run `pnpm exec vite build` (esbuild, transpile-only — it never type-checks). Typechecking is
`make typecheck` in `lint.yml`. A type error therefore surfaces **earlier** and blocks **everything**,
which makes the import-cycle atom a harder constraint than a per-leg failure, not a softer one.

### Rule 3 — resolve straddlers with three mechanisms, in this priority order

A **straddler** is one file whose hunks belong to different parts. Try the mechanisms in order and
record which one you used; never reach for a later mechanism while an earlier one applies.

**3a. Inert superset (first choice).** Assign the whole file to the **earlier** part when the later
part's hunks are **dead code there** — an unimported module constant, an unreferenced type
declaration, an unused config key, an additive-only test fixture. The later part's diff then simply
does not contain the file, and the union still reproduces the original tree exactly.

**Get the logic the right way round: inertness makes an assignment *safe*, it does not *choose* the
direction.** The direction is forced by the hunks that are **not** inert — the file goes to whichever
part cannot compile, import or pass without its own hunks, and 3a then merely licenses carrying the
*other* part's hunks along for the ride. Read backwards ("the later part's hunks are inert, so put
the file wherever") a planner can resolve a straddler toward the **later** part and ship a red
earlier part. `user_strs.py` below is exactly that trap: it is forced into the earlier (backend) part
by its rate-limit constant, which `create_members.py` imports and returns at the daily cap; assigning
it to the frontend part instead fails the backend part with an `AttributeError` on
`MEMBER_FAILURE.MEMBER_ADD_RATE_LIMITED`. So: **find the forcing hunk first, then check the rest is
inert.** If hunks force the file *both* ways, 3a does not apply — go to 3b, then 3c.

Worked cases:

- `backend/utils/strings/user_strs.py` — the later part's 14 `MEMBER_ADD_*` display strings are plain
  module-level assignments that nothing imports until the later part lands. Inert in the earlier part.
- `backend/utils/all_routes.py` — the later part's hunk adds one key to `generate_routes_js()`'s
  emitted JSON. `frontend/lib/config.ts` reads route keys **by name** and never enumerates or
  validates the raw object (`rawConfig.routes` is a `Record<string, string>`), so an extra key is
  inert. **Caveat found when this was re-derived against the real diff:** in PR 749 this file is
  arguably **not a straddler at all** — `generate_routes_js()` calls
  `url_for(MEMBER_ROUTES.CO_MEMBER_CANDIDATES, utub_id=-1)`, which raises `BuildError` at
  app-config time if the route is absent, so **all** its hunks are mandatory in the backend part and
  the assignment is `atom:route + all_routes.py linkage`, not `inert-superset`. The destination is
  the same either way; the *rule label* is not. Re-derive it rather than copying this citation.

**Prove inertness by running the earlier part's gate, not by inspection** (Skill Step 4).

**Carve-out — inertness is per-map, not per-file.** In `all_routes.py`, a `JS_ROUTES` /
`ADMIN_JS_ROUTES` key or a `generate_routes_js()` entry pointing at a JS module that does not exist
yet **is** inert: nothing in the audit checks that the module file exists. But a `NO_JS_ENDPOINTS` or
`INDIRECT_JS_ENDPOINTS` key naming an endpoint that is **not live in that part** is a hard
`no_js_entry_unknown` / `indirect_entry_unknown` finding and fails `Endpoint Registry Audit`.
Conversely, a part that adds an `@api_route` **must** carry its linkage entry in the same part, or
`find_api_routes_without_js_linkage()` fails it. Read the hunk, not the filename.

**3b. Amend-in-later-part (second choice).** When the earlier part's whole-file assignment would
**misdescribe a later part's artifact** — a registry or doc row whose cell names a module that does
not exist yet — assign the whole file to the earlier part **as usual**, and let the later part's own
diff carry a further, targeted amendment correcting just that content. The union invariant still
holds because the final content after the later part equals the original content; verify it by
re-running the plain tree-hash union check (Skill Step 3) afterward, with no special-casing.

This is the `docs/endpoints/ENDPOINT_REGISTRY.md` case **as the repo stands today** — resolve the
file's real path and its real generator at `BASE` before applying any of the next paragraph, per
Rule 2's "verify them at the branch" note. In PR 749 the same file was the root
`ENDPOINT_REGISTRY.md`, hand-maintained, with **no** JSON sibling, no generator and no audit; there
the amendment is a plain targeted edit of that one cell and the "pair is an atom" clause is vacuous.

Where the generator *does* exist, **the amendment must be produced by the generator, not by hand.** `endpoint-registry.json` is canonical and the `.md` is rendered from it;
`flask endpoints audit --strict` runs `diff_markdown()`, which fails with `markdown_stale` unless the
committed markdown is **byte-exactly** `render_markdown(committed json)`. So:

- the later part runs `make generate-endpoints` and commits **both** `docs/endpoints/endpoint-registry.json`
  and `docs/endpoints/ENDPOINT_REGISTRY.md` — the pair is itself an atom (Rule 2);
- never hand-edit the `.md` cell (the repo's own rule: the registry is generated, never hand-edited);
- the earlier part still carries the **substantive** rows, because the repo requires the registry to
  be regenerated in the same commit as the endpoint change, and `diff_registry()` compares the
  committed JSON against the **live app** in whichever part is under test.

**3c. Hunk-splitting (last resort only).** Use it only when hunks are genuinely **active in both
parts** — neither 3a nor 3b applying. Perform it with the **Edit tool** on the checked-out file,
never with patch surgery: hand-editing a `.patch` file breaks line counts and context, and `--3way`
is forbidden on the first pass because it can synthesize content and silently violate the union
invariant. Flag every hunk-split seam explicitly in the output — it is the one case where two parts
legitimately touch the same path, so Skill Step 3's disjointness check must be told about it.

In PR 749, **3 of 78 files straddled and none needed 3c**: two resolved by 3a, one by 3b.

### Rule 4 — emit an unrelated-changes part

Files with **no dependency in either direction** — nothing in them imports or is imported by the
other parts' changes — become their own part. In PR 749 that was **18 of 78 files**: an inline
saved-tick SVG, a multi-select toggle visibility fix, and a Postgres-sidecar readiness race in
`docker/backup-pipeline-test.sh`. A splitter that assumes a clean two-way layer cut mis-assigns a
quarter of the diff.

This part is **orderable anywhere** — first, last, or in parallel — and landing it first is the
cheapest way to shrink the remaining diff. **Order-free is not the same as further divisible:** the
bucket is usually several unrelated streams, and they routinely share a test file. In PR 749 its 18
files are three streams (saved-tick SVG 7, multi-select toggle 10, Postgres-sidecar readiness 1), and
three vitest files carry hunks from **two** streams at once — so subdividing this part would force
3c hunk-splitting on them. Keep it as **one** part unless the user asks otherwise, and say that
splitting it further costs 3c. **Flag it prominently:** it is usually a sign that
unrelated work rode along, and it **needs its own GitHub issue**, because it is not part of the
original work and must not carry the original plan's `Closes #<issue>`.

### Rule 5 — order the parts by the dependency arrow, and state that the arrow is *verified*

For each ordered pair, answer mechanically — do not assume a layer convention:

1. **Generated-artifact ownership** (Rule 1): does one part contain a generator input whose output
   lives in the other?
2. **Type/symbol resolution:** do the part's new imports or type references name symbols defined only
   in another part?
3. **Bridge crossings:** route tables, string bridges, config injection — declared per repo.
4. **Runtime-only edges (the dangerous ones):** edges that typecheck and unit-test fine because the
   test harness **mocks the far side**. Detect them by diffing the part's changed route and string
   keys against the repo's test-harness mock file.

For PR 749 the arrow is backend → frontend, and it is **verified, not assumed**: the frontend cannot
typecheck without the generated types, **and cannot carry them itself**, because `types-staleness.yml`
regenerates them from the unchanged backend and fails on the diff. The reverse order merges cleanly
because the backend's forward-facing additions are all inert (Rule 3a).

### Rule 6 — compute each part's plan step-membership list

Walk the original plan's **per-step file lists**. For each part, record **every plan step number that
names at least one file assigned to that part**.

**Read each step's validation / verification prose too, not only its to-do file list.** A plan
routinely names a file *only* in a step's "verify by running X" note — in PR 749,
`tests/unit/test_api_route_decorator.py`, `tests/integration/system/test_metrics_domain_no_pii.py`
and `tests/integration/system/metrics_helpers.py` appear in Step 9's validation prose and in no
step's file list at all, so a list-only walk maps them to no step and under-reports the membership.

- The list **need not be contiguous** — a part's files may come from non-adjacent steps. Emit a
  comma-separated list of step numbers (`2, 4, 5`), never a range like `2-5 of 7`.
- The **unrelated-changes part maps to no plan steps by construction**: record the explicit literal
  `none`, never an empty list and never an omitted entry.
- A branch with no plan in the central store: every part records `none`.

This list is persisted verbatim in the manifest's `plan-steps` column and emitted verbatim as each
part PR's `Plan-Steps:` header, so it is never re-derived in a later, context-free invocation.

---

## 2. Required output

Write **one markdown file** to the path the orchestrator gives you (under `<scratch>/`) with
the **Write tool** — never `cat >`, `cat <<EOF`, `tee`, `echo >` or `printf >`
(`block-bash-writes.sh` denies all of them, and a heredoc containing braces and quotes trips the
brace+quote guard). Reply with the single line `Written to <path>` and nothing else.

### 2.1 Parts summary

| part | name | files | +lines | −lines | depends on | plan-steps |
| ---- | ---- | ----- | ------ | ------ | ---------- | ---------- |

One row per part, **in the proposed merge order**. `depends on` is the part(s) that must merge first,
or `none` for an order-free part.

### 2.2 Per-part file assignment

For each part, a table of every file with its status and the rule that assigned it:

| file | status (`A`/`M`/`D`) | rule | justification |
| ---- | -------------------- | ---- | ------------- |

`rule` is one of `generator-ownership`, `atom:<atom name>`, `inert-superset`,
`amend-in-later-part`, `hunk-split`, `unrelated`, or `plain` (unambiguously one part's own file).
`justification` is one sentence naming the mechanism's evidence — the enforcing CI job, the importing
symbol, the mocked key. A file with `rule: plain` still needs a one-clause reason.

### 2.3 Straddlers

One section per straddling file: its hunks, each hunk's owning part, which of 3a/3b/3c resolved it,
and — for 3b — exactly what the later part amends and which generator produces that amendment. For
any 3c hunk-split, state the file path explicitly under a heading **`HUNK-SPLIT SEAMS`** so Skill
Step 3's disjointness check can expect the overlap.

### 2.4 Atoms honoured

List every atom you detected in the diff and the single part it landed in. An atom spanning two parts
is a **blocking** finding, not a note.

### 2.5 Ordering argument

The verified dependency arrow per Rule 5, with the four mechanical answers for each pair, and an
explicit statement of which part(s) are order-free.

### 2.6 Risks and open questions

Anything the orchestrator must put in front of the user: an unrelated-changes part that needs its own
issue, a straddler you resolved with low confidence, a file whose generator you could not identify, a
part whose local gate you expect to be slow or expensive.

### 2.7 Pathspec files

For each part, the exact list of paths to write into `<part>.paths`, one path per line. No globs, no
comments, no blank lines, no leading `./` — literal paths as `name-status.txt` spells them. The
orchestrator persists these under `<plan-dir>/split/`, not `tmp/`.

**Why the format is that strict: the file is never passed to git as a file.** `git diff` has **no**
`--pathspec-from-file` flag (verified on git 2.53.0: it exits **129** with git's own usage text —
the flag exists on `add`, `commit`, `checkout`, `reset`, `rm`, `stash` and `grep`, not `diff`). The
orchestrator **splices these lines in literally** after `--` when it builds each part's patch in
Skill Step 3, so every line must be a bare usable path with nothing git would have to interpret.
