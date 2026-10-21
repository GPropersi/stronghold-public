# CI Gates — the Skill Step 4 per-part local green gate

Skill Step 4 of `/split-pr` proves **each part green on its own, locally**, before a CI cycle or a
human review is spent on it. The original branch was green as a whole; a part is a *partial tree*, and
partial trees fail in ways the whole never did. This file holds the gate tiers, the per-part recipes,
the analysis of **which gates lie**, the local-target ↔ CI-job map, the manifest `status` write, the
cleanup sequence and a verified-facts ledger.

It is split into a generic half (§0-§5, true of any repo) and a per-repo half (§6, behind a **Stack**
tag) so SKILL.md itself stays repo-agnostic.

---

## 0. The prefix rule, the one-time init, and the two stop rules

- **Gate the PREFIX of the merge order, not the lone part.** SKILL.md's second invariant is **prefix
  greenness** — *every prefix of the merge order must be independently green* — and that is what CI
  will actually see: part *k*'s real PR branch is cut from `origin/<default-branch>` **after** parts
  *1…k-1* have merged, so its CI tree is `BASE + p1 + … + pk`. Skill Step 3, by design, builds each
  part **alone** at `BASE`, which is the right tree for the union proof and the **wrong** tree for a
  gate: a part that consumes an earlier part's artifact is red at `BASE` *by construction*. So, before
  gating part *k*, **merge part branches *1…k-1* into part *k*'s verification worktree** (plain
  `git merge`, in merge order — trivial by construction, exactly as `invariant-checks.md` §5 argues).
  Part 1 needs no merge, since its prefix is itself.
  - **Verified**, and it is not hypothetical: a frontend part whose only change was
    `type X = SuccessResponse<"newOp">` failed `make typecheck` alone at `BASE` with
    `error TS2344: Type '"newOp"' does not satisfy the constraint 'keyof operations'` (exit 2), and
    **passed** once the backend part that generates that operation was merged in. Gating it alone
    would have tripped Stop rule 1 and aborted a perfectly legal split.
  - The verification branches are throwaway (§5 deletes them) and Skill Step 5 rebuilds every part
    from its `.paths` file, so mutating them with these merges costs nothing. **Never** let the merge
    change what a part's `.paths` says it contains.
  - Consequence for marker derivation: run any diff-scoped marker tool (§3) **before** the prefix
    merges, or it reports the prefix's files instead of the part's own.
- **Initialize each verification worktree once, before ANY gate.** The repo's worktree-creation target
  may do far less than the gates need. For app-main, `worktree-new` only links `.env`/`secrets`; it
  installs **no** dependencies and starts **no** stack, so the worktree needs **both**
  `make -C <wt> tools` (host toolchain + `frontend/node_modules`) **and** one
  `make -C <wt> up d=1` **before the first gate of any tier** — including the ones §1 calls tier 0 and
  tier 1. Skipping it does not merely fail a gate, it can **poison the worktree** (§6d-7). Verified
  failures: §6d-6, §6d-7.
- **Everything runs through the repo's `Commands` seam and its `make` targets** — never a raw
  `pytest`/`vitest`/`tsc`/`docker` invocation. `Bash(make:*)` is allowlisted and sandbox-excluded
  centrally (`~/code/CLAUDE.md` → Makefile-First Command Policy), so a Makefile-covered repo needs no
  new permission entries. No target for a gate you need ⇒ the gate does not exist yet: **STOP and
  report**, do not improvise the raw command.
- **Each gate runs in that part's own verification worktree, by absolute path**, as
  `make -C <worktree> <target>`. Never `cd` first: the Bash tool's cwd does not persist between calls,
  and a `cd` compounded with `make` is blocked twice over (`block-compound-commands.sh`, and chaining
  defeats `sandbox.excludedCommands`). Each command is **its own Bash call**.
- **Those worktrees are the ones Skill Step 3 created** with the repo's own worktree target plus its
  base-ref override (`make worktree-new name=… b=… from=<BASE>` for app-main) — **not**
  `git worktree add`, which is hook-denied in every sub-repo, and **not** `make wt-new`, which cannot
  pin a base. See `invariant-checks.md` §2. **Nothing is cleaned up until every part has passed §1-§3**,
  because a failure sends you back into that part's tree to read it.
- **Redirect every gate's output to a file and read the file** — `make -C <worktree> <target>
  > <scratch>/<part>-<target>.txt 2>&1`, then Read it. Never capture a test run inline
  (`~/code/CLAUDE.md` → Test output). Delete the files in §5.
- **Stop rule 1 — a local failure stops the run.** Report the part, the target, the failing test and
  the excerpt from the redirect file. Then investigate it properly (`~/code/CLAUDE.md` → Test
  Failures: Investigate, Don't Dismiss). A partial tree's failure is *information about the slice*,
  almost never a flake.
- **Stop rule 2 — never re-slice silently to make a gate pass.** Moving a file from one part to
  another changes the manifest **the user accepted** in Skill Step 2, invalidates the partition proof
  in `invariant-checks.md` §1, and changes what each PR will claim to contain. A gate failure that
  really is a slicing defect goes **back to the user** with the proposed re-assignment, then back
  through Skill Step 2's acceptance and Skill Step 3's rebuild. Never hand-edit a `.paths` file.

---

## 1. The three gate tiers

A gate costs what its *environment* costs, not what its assertions cost. Classify every target before
running anything, then run cheapest-first — a failure at tier 0 costs seconds, the same failure found
at tier 2 costs a stack rebuild.

| Tier | Environment                                                | Typical cost | What it can catch                                                                     |
| ---- | ---------------------------------------------------------- | ------------ | ------------------------------------------------------------------------------------- |
| 0    | host only (no container)                                   | seconds      | cross-file type and import resolution, unit tests, lint                               |
| 1    | containers, but no running app stack (one-off `run --rm`)  | ~1 min       | bundler/import resolution, image builds, self-contained E2E harnesses                 |
| 2    | the dev app stack **up** (`exec` into a running service)   | minutes      | backend integration tests, generator/registry audits, anything needing a DB           |
| 3    | the stack **rebuilt with production-shaped assets**        | many minutes | everything the dev stack's mocks, dev server and hot-reload hide                      |

**Ordering rules.**

0. **The one-time worktree init in §0 comes before tier 0.** "Cheapest tier first" is an ordering over
   *gates*, not a licence to run the first gate in an uninitialized worktree. A tier's *environment*
   cost is what the table prices; the worktree's **own** setup cost is paid once, up front, whatever
   tier runs first. In particular a tier-1 "one-off container" target is only stackless **after** the
   worktree has been through one stack start, because that is what generates the compose env file the
   one-off container is built and run with (§6d-7).
1. Tier 0 first, always, and `typecheck`-class gates before test-class ones at the same tier.
2. A tier-3 (`-built`-class) target typically **tears the running dev stack down and rebuilds it**, so
   it invalidates tier 2 for that worktree. Run **all** tier-0/1/2 gates for a part, then its single
   tier-3 run **once, at the end, outside any iteration loop**. Going back to a tier-2 gate afterwards
   means bringing the dev stack up again.
3. Tier 2 needs the stack **up** before the first `exec`-based gate — that is a separate `make` call,
   not something the test target does for you on every repo.

**What a part inherits green and what it does not.** A part's file contents are byte-identical to the
original green branch (`git apply` of a diff restricted to a pathspec), so every **per-file** check is
inherited green: formatters (ruff/prettier/shfmt), manifest-pinning checks, YAML/action lint. What is
**not** inherited is every **cross-file** check — type resolution, import resolution, test collection,
generated-artifact freshness, registry audits — because those read files the part deliberately left
behind. Spend the budget there; do not re-run the formatter gate to feel safe.

---

## 2. Which gates lie

The cheap tiers lie **wherever the repo's own test scaffolding stands in for the thing the split cut
in half.** That is not a bug in the repo: a unit test *should* mock its environment. It is a property
of splitting, and it means a green tier-0 run on a frontend-shaped part carries almost no information.

Three recurring blindness patterns, in the order they bite:

1. **Hand-mocked seam tables.** The unit-test setup synthesizes the config object the backend normally
   injects — route table, string table, feature constants. A part that uses `config.routes.newThing`
   passes unit tests because the *mock* defines `newThing`, while at runtime the backend half that
   registers and injects that key lives in another part, so the value is `undefined`. Nothing short of
   a run against the **real** rendered config catches it. (**Stack: app-main** §6d-1 — verified.)
2. **Generated-artifact staleness.** A generator lives in one part and its committed output in
   another, or the generator's input (a route, an event, a dimension) moved without the output. The
   unit tier never runs the generator; a dedicated CI job does, and fails on the diff. The local
   counterpart is "run the generator, then assert the tree is unchanged".
3. **Registry-enumerating suites.** A suite that iterates a registry — every metrics dimension, every
   endpoint, every event — breaks the moment one part adds a member the other part's assertions do not
   know about. These suites are easy to mis-slice because they live nowhere near the feature: they
   must ride with the part that changes the **registry**, not with the part that changes the UI.

**The rule this yields.** For every part, name out loud what its cheap tier mocks away, and pick at
least one gate that does **not** mock it. **At least one tier-3 / full-stack run per part is
non-negotiable** — including for a part that looks purely additive.

---

## 3. Per-part gate recipes

Parts come in three shapes. Match the part to a shape by what it changes, not by its name.

**Backend-shaped part** (routes, models, limiters, generators, server-side strings):

- the part's own domain marker(s), plus the **API-surface** marker and the **unit** marker;
- **plus the marker that owns any registry-enumerating suite the part touches** — these are the §2-3
  suites, and they are usually under a marker you would never guess from the feature name;
- the coverage/registry audits the repo runs as their own CI jobs;
- the generator, followed by an **exit-code diff of the generated directory** (`git -C <worktree> diff
  --exit-code <generated-dir>`), which is exactly what the staleness CI job asserts;
- and, **once at the end, before pushing**, the full-stack UI run for the domain the part's
  server-side change governs — to prove the **pre-existing** UI suite (rate limits, validation
  messages, error codes) still passes against the part's new server behaviour. The pre-existing suite
  is the one most likely to break, because nobody wrote it with this change in mind.

**Frontend-shaped part** (JS/TS modules, templates, styles):

- the cross-file host gates (typecheck, unit tests, bundler build) — knowing from §2-1 that the unit
  tier **lies** about the runtime seam;
- then the full-stack UI run for that part's marker. **Non-negotiable, no exceptions.** Without it a
  frontend part can be green locally, green in the cheap half of CI, and broken in the browser.

**Unrelated-changes part** (the bucket Skill Step 2 carves out for drive-by fixes):

- the host-tier gates its file types imply (unit tests, bundler build);
- the full-stack UI run for whichever marker owns the UI test that covers the drive-by fix;
- and the **dedicated harness** for any infrastructure file it touches (a backup/deploy/provision
  script has its own E2E harness and its own CI job; the generic suites never exercise it).

For all three: the part's gate set is derived from *the files in the part*, and if a part's files imply
a gate with no `make` target, that is Stop rule 1.

**Let the repo derive the marker set if it can — don't guess it.** A repo with a diff-scoped test
selector already encodes the file → marker mapping that §6b's sequences only *illustrate*. Run it per
part and use its answer as the floor, then add the §2 blind-spot gates on top. For app-main that is
`make -C <wt> affected-markers` (preview; `make test-affected` runs them), and it must be run **before**
the §0 prefix merges, or it reports the prefix's files rather than the part's own. Two things it tells
you that a hand-derived list will not:

- the markers attached to files you would never associate with the feature — it resolves
  `docs/endpoints/*` to `cli, unit`, which is exactly the §2-3 registry-enumerating-suite trap;
- which files it considers **broad**. A file it cannot scope (for app-main, `backend/utils/all_routes.py`
  and `frontend/types/*` both print `— (broad: …)`) escalates its part's verdict to
  `Integration: everything` / `UI: everything`. That is a real cost signal, not noise: **a part holding
  a broad-impact file has the full matrix as its honest gate set**, so either budget for it or say
  plainly in the hand-back which markers you ran instead and why. Do not quietly substitute a narrow
  marker for an `everything` verdict.

**A backend part that governs no UI domain still owes a tier-3 run.** §6b's backend sequence ends with
"the full-stack UI run for the domain the part's server-side change governs", which presumes such a
domain exists. For an infra/probe/registry-shaped backend part there is none. §2's rule is still
absolute, so pick the **cheapest** UI marker as a boot-and-render smoke run (for app-main, `splash_ui`)
and record in the hand-back that it is a smoke run, not a behavioural gate for that part.

---

## 4. The manifest `status` write — `built-and-verified`

**Skill Step 4 is the only step that writes the manifest's `status` column.** Write
`built-and-verified` for a part **as soon as that part's own gates pass, including its mandatory
tier-3 run** — per part, incrementally, not once at the end for all parts.

- **Skill Step 3 writes nothing.** Its partition check, its per-part builds, its provenance commits
  and its union tree-hash proof all leave every part at `planned`. A `status` written there would mean
  "built", but the column's meaning is "**built and locally proven green**".
- **This is the write the resume path reads.** Skill Step 1b, on a later invocation, skips Skill
  Steps 3-4 for every part already marked `built-and-verified`. The expensive thing that skip avoids
  is precisely the tier-3 run — which is why the status must never be written before it passes.
- A part that fails a gate keeps `planned`. Never write `built-and-verified` "provisionally".
- Sync the manifest to the stronghold after the writes (`.claude/scripts/stronghold-sync.sh`), so a
  resume in a fresh session sees them.

---

## 5. Cleanup — once every part has passed, and not before

Order matters: the worktree must go before its branch (a checked-out branch cannot be deleted), and
nothing goes until the **last** part is green.

**"Nothing goes" means nothing is *deleted*.** It does not mean every part's stack stays **up**: once a
part is green, `make -C <wt> down` it before moving to the next part. The worktree, its patch and its
branch stay (a later failure sends you back into that tree to read it), but a running stack holds
`min_run_gb` for no reason and is the main way a 3-part split exhausts `suites_ceiling` (§6d-5).

1. **Remove every verification worktree** — the N per-part worktrees **and** the single union worktree
   from `invariant-checks.md` §5. Use the removal form the repo actually supports (see the table
   below); it must also dispose of whatever the gates *started* — containers, volumes, images, a
   throwaway database — or tier-2/3 gates leave a running stack per part behind.
2. **Delete the throwaway verification branches** (`split/<topic>-verify-<part>` and
   `split/<topic>-verify-union`). These are not the part PR branches: Skill Step 5 cuts those fresh
   from the then-current `origin/<default-branch>`.
3. **Delete the run's ephemeral scratch** — `<scratch>/*.patch` and the gate redirect files.
4. **Keep the canonical pathspec files.** `<plan-dir>/split/<part>.paths` lives **durably** in the
   stronghold plan folder (Skill Step 2e's schema). Skill Step 5 rebuilds each part's patch from it,
   and the resume path reads it. **Never delete it, never hand-edit it.**

| Need                                | Form                                                | Status                                                                                                                    |
| ----------------------------------- | --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| remove a worktree + its live stack  | `make -C ~/code wt-rm REPO=<repo-dir> BRANCH=<b>`   | **preferred** — finds the worktree **by branch**, then delegates to the repo's own `worktree-rm` (verified, §6e-7)        |
| remove a worktree, nothing else     | `git worktree remove <path>`                        | permitted (only `git worktree add` is hook-denied) but leaves the gates' containers/volumes/images and databases behind   |
| force-remove a worktree             | `git worktree remove -f …`                          | avoid — `allow-git-ops.sh` treats `worktree:-f` as destructive and defers to a prompt; a clean worktree needs no force    |
| delete the throwaway branch         | `git -C <primary> branch -D <branch>`               | permitted and **prompt-free**: `allow-git-ops.sh` only *defers* `branch:-D`, then `Bash(git branch:*)` allows it (§6e-8) |
| relocate the session                | the `ExitWorktree` tool                             | **not applicable** — `EnterWorktree` is hook-denied, so the session was never inside the worktree to exit it              |

**`git branch -d` is not a substitute for `-D`.** A verification branch is rooted at `BASE` and merged
nowhere, so `-d` refuses it. **`-D` runs silently — expect no prompt** (verified live in plan 9d:
deleting two branches in one `git branch -D` produced none; `disposition.md` §2b has the hook chain).
These are throwaway verification branches, so prompt-free is the right behavior here — unlike the
*original* branch in Skill Step 6, which is irreversible and needs the skill's own `AskUserQuestion`
gate. Several branches can go in a single `git branch -D` call.

**Skill Step 6's / the plan's dry run reuses this same pipeline and must perform the identical
cleanup** — a dry run that leaves N worktrees and N stacks behind costs the next run its capacity.

---

## 6. Stack: app-main

Everything below is specific to the `app-main` repo and verified against its tracked `Makefile` and
`.github/workflows/` at the time of writing (citations in §6e). Another repo gets its own section;
SKILL.md never names these targets.

### 6a. Command catalog, by tier

| Tier | Target                                    | Mechanism                                                        | Notes                                                                  |
| ---- | ----------------------------------------- | ---------------------------------------------------------------- | ---------------------------------------------------------------------- |
| 0    | `make typecheck`                          | host `tsc --noEmit` × 3 tsconfigs (app, test, node)              | the hardest cheap gate; see §6c — CI blocks everything on it           |
| 0    | `make test-js`                            | host vitest (`--config frontend/vitest.config.ts`)               | "no stack needed"; **lies** on the config seam (§6d-1)                 |
| 0    | `make lint` / `make format-check`         | ruff, eslint, shellcheck, action lint, pin/lockfile checks       | formatting is inherited green (§1); `lint` matters only on moved files |
| 1    | `make vite-build`                         | `compose run --rm --no-deps vite pnpm exec vite build`           | one-off container; no running stack                                    |
| 1    | `make test-backup-pipeline`               | builds `Dockerfile.Local` + `Dockerfile.Workflow`, runs the E2E  | throwaway containers, not the hub; holds 1 budget token                |
| 2    | `make up d=1`                             | `compose up` (detached)                                          | **prerequisite** for every tier-2 gate below                           |
| 2    | `make test-marker-parallel m=<marker>`    | `compose exec web pytest -m <marker> -n …`                       | needs the stack **up**; `*_ui` markers also start hub Playwright       |
| 2    | `make audit`                              | `compose exec web flask metrics audit --strict`                  | **not** host-only — execs into the running `web` container             |
| 2    | `make audit-endpoints`                    | `compose exec web flask endpoints audit --strict`                | same; the local twin of the Endpoint Registry CI job                   |
| 2    | `make generate-types`                     | `exec web flask … generate` ×6 + `run --rm vite` openapi-ts      | writes into `frontend/types/`; diff-check it afterwards                |
| 3    | `make test-marker-parallel-built m=<m>`   | `start-built` (teardown + rebuild with built assets) then pytest | the only honest frontend gate; run **last**                            |
| 3    | `make test-ui-parallel-built`             | same, whole UI suite                                             | too broad for a part gate; use the marker form                         |

**`vite-build`'s tier-1 label is conditional.** It is a one-off `compose run --rm --no-deps` container,
but the container is built and run with `HOST_UID`/`HOST_GID` from `docker/.capacity.generated.env`,
which only `_capacity-fresh` writes — and `vite-build` does **not** depend on it (`Makefile:464-465` has
no prerequisites at all). In a worktree that has never run `make up`, it is therefore **not** a safe
first gate: see §6d-7, which is a poisoning failure, not a transient one. The same applies to every
other `RUN_VITE` target, including the tier-2 `generate-types`.

**`n=` is optional.** `test-marker-parallel[-built]` derives its worker count from the capacity model
(`APP_N_UI` / `APP_N_INT`) and `_require-n-fits` **refuses** an explicit `n=` that does not fit live
memory. Omit `n=` unless a specific count is wanted; `n=8` is an example, not a requirement.

**`test-js-built` is an alias of `test-js`.** The "anything `-built` needs the rebuilt stack" rule has
exactly this one exception — vitest is host-native and mode-independent.

### 6b. Gate sequences

Each line is its own Bash call, with `> <scratch>/<part>-<n>.txt 2>&1` appended.
`<wt>` is that part's verification worktree.

**Every sequence below is preceded by §0's two preambles, in this order** — they are not optional and
not part of the tier ordering:

```
git -C <wt> merge --no-edit <part-branch k-1>   # …and 1…k-2, in merge order; skip for part 1 (§0)
make -C <wt> affected-markers                   # derive this part's markers BEFORE the merges (§3)
make -C <wt> tools                              # host toolchain + frontend/node_modules (§6d-6)
make -C <wt> up d=1                             # generates the compose env file; unblocks RUN_VITE (§6d-7)
```

(`affected-markers` is listed after the merge only for layout; run it first — see §3.)

**Backend-shaped part** (example: a members limiter + a new `MEMBER_ADDED` metrics dimension)

```
make -C <wt> typecheck
make -C <wt> test-js
make -C <wt> test-marker-parallel m=members          # the part's own domain marker
make -C <wt> test-marker-parallel m=mobile_api       # the /api/v1 bearer-token surface
make -C <wt> test-marker-parallel m=unit
make -C <wt> test-marker-parallel m=cli              # ← tests/integration/system/ lives here (§6d-2)
make -C <wt> audit                                   # metrics event coverage
make -C <wt> audit-endpoints                         # only if the part changes routes
make -C <wt> generate-types
git -C <wt> diff --exit-code frontend/types/         # must print nothing and exit 0
# once, at the very end, before pushing — never inside an iteration loop:
make -C <wt> test-marker-parallel-built m=members_ui
```

**Frontend-shaped part** — remember it is gated on the **prefix** (§0), so the backend part it consumes
is already merged into `<wt>`; alone at `BASE` its `typecheck` fails by construction.

```
make -C <wt> typecheck                               # the gate the dependency arrow actually trips
make -C <wt> test-js                                 # green here proves almost nothing (§6d-1)
make -C <wt> vite-build
make -C <wt> test-marker-parallel-built m=<marker>_ui   # non-negotiable; run EVERY _ui marker the
                                                     # part's files resolve to, not just the first
```

**Unrelated-changes part**

```
make -C <wt> typecheck                               # the prefix carries the earlier parts' TS
make -C <wt> test-js
make -C <wt> vite-build
make -C <wt> test-marker-parallel-built m=urls_ui    # the marker that owns the drive-by's UI test
make -C <wt> test-backup-pipeline                    # ONLY if the part touches docker/ or an infra
                                                     # script; otherwise say "not applicable", never
                                                     # run it for show and never drop it silently
```

### 6c. Local target ↔ CI check map

app-main's `CI.yml` is a fan-out of reusable workflows. **`format` → `lint` → everything else**: every
remaining job declares `needs: 'lint'`, and `lint` declares `needs: 'format'`. So a typecheck failure
does not merely fail one check — **the entire test matrix never runs.**

| CI check (display name)        | workflow → job                                                                       | what it runs                                             | local gate                                            |
| ------------------------------ | ------------------------------------------------------------------------------------ | -------------------------------------------------------- | ----------------------------------------------------- |
| Code Quality                   | `format.yml` → `Format`                                                              | `mise-config-check`, `format-check`                      | `make format-check` (inherited green — §1)            |
| Code Quality                   | `lint.yml` → `Lint`                                                                  | `make lint`, then **`make typecheck`**                   | `make typecheck` ← blocks all of the below            |
| Test                           | `test.yml` → `JS-Unit-Tests`                                                         | vitest                                                   | `make test-js`                                        |
| Test                           | `test.yml` → `Tests-Integration` (10 legs)                                           | pytest per marker (`unit`, `members`, …)                 | `make test-marker-parallel m=<marker>`                |
| Test                           | `test.yml` → `Tests-UI` (13 legs)                                                    | pytest per `*_ui` marker, **built assets**               | `make test-marker-parallel-built m=<marker>_ui`       |
| Generated Types Freshness      | `types-staleness.yml`                                                                | regenerate `openapi.json` + `api.d.ts`, diff             | `make generate-types` + `git diff --exit-code`        |
| Event Coverage Audit           | `event-coverage-staleness.yml`                                                       | `flask metrics audit`, regen `metrics-flows`             | `make audit` + the same `frontend/types/` diff        |
| Endpoint Registry Audit        | `registry-staleness.yml`                                                             | `flask endpoints audit --strict`                         | `make audit-endpoints`                                |
| Test Build Docker Container    | `test-build-image.yml` → `ProdBuild`                                                 | build `app-prod` / `app-workflow` + smoke                | (no cheap local twin; relies on CI)                   |
| Test Build Docker Container    | `test-build-image.yml` → `BackupPipelineTest` (display name **Backup Pipeline E2E**) | `docker/backup-pipeline-test.sh` against `app-prod:test` | `make test-backup-pipeline`                           |

**`make test-backup-pipeline` vs the CI job.** Same script, same driver, same assertions; CI passes
images built from the **production** `Dockerfile`, the local target passes `app-local-web:test` built
from `docker/Dockerfile.Local`. The difference is the image's build shape, which is immaterial to a
Postgres-sidecar **timing** fix in the harness script itself — so the local run is a faithful gate for
that class of change, and not for a change to the production Dockerfile.

**`tsc --noEmit` is NOT part of the 13 `Tests-UI` legs.** It is the last step of `lint.yml`'s
`Code Quality` job. `make typecheck` is therefore an **earlier and harder** gate than a UI leg — it
gates the UI legs' existence.

### 6d. Traps

1. **`frontend/test-setup.ts` makes vitest blind to the whole backend seam.** It builds an
   `appConfig` object by hand — the complete `routes` table, `constants`, and `strings` — and injects
   it as the `#app-config` script element that `frontend/lib/config.ts` reads. Its own comment says
   why: *"Vitest runs in Node with no Flask app context, so APP_CONFIG must be hand-mocked."*
   Consequences, precisely:
   - A frontend part that reads `config.routes.<newKey>` or a new `strings.<KEY>` **passes vitest**
     even when the backend part that registers the route and defines the string is absent. At runtime
     both are `undefined`.
   - `make typecheck` catches **half** of it: `AppRoutes` is a declared interface in `lib/config.ts`,
     so a consumer naming a route id that the interface does not declare fails tsc. But the mapping
     itself reads from `rawConfig.routes`, typed `Record<string, string>`, and `strings` is
     `Readonly<Record<string, string>>` — so the key's *existence at runtime* is outside the type
     system entirely, and strings have no per-key typing at all.
   - Therefore only a tier-3 run, where Jinja renders `#app-config` from the real Flask config, can
     catch it. This is why the built UI run is non-negotiable on a frontend part.
2. **`tests/integration/system/` is marked `cli`, not `unit`.** Both metrics-domain suites
   (`test_metrics_domain_pipeline_e2e.py`, `test_metrics_domain_no_pii.py`) carry
   `pytestmark = pytest.mark.cli`, and they enumerate the metrics dimension registry — which is how
   they broke on a new `MEMBER_ADDED` dimension in the original plan's final sweep. Two obligations:
   - those files must **ride with the backend part** that changes the registry, never with the UI part;
   - the backend gate must include **`m=cli`**. A gate set of `members` + `mobile_api` + `unit` runs
     neither file, so the very trap the plan calls out would pass locally and fail in CI's
     `CLI Integration Tests` leg.
3. **`make audit` and `make generate-types` are not host-only.** Both `exec` into the running `web`
   container, so a tier-2 stack (`make up d=1`) must be up first. Forgetting this reads as a cryptic
   compose error, not as a missing prerequisite.
4. **A `-built` run destroys the dev stack it shares the worktree with.** `test-marker-parallel-built`
   depends on `start-built`, which prunes, tears the spoke down and rebuilds it with pre-built Vite
   assets. Any tier-2 gate you still need must run **before** it, or `make up d=1` again after.
5. **Capacity is finite and shared.** N+1 verification worktrees are N+1 stacks. Run
   `make -C ~/code wt-capacity REPO=app-main` before starting a suite (redirect, Read, delete) and
   respect `agents_max` / `suites_ceiling` (`~/code/CLAUDE.md` → Worktree Discipline (4)). Re-read it
   **between** parts, not once at the start: another session on the same host moves these numbers
   (observed mid-run: `suites_ceiling` 3 → 2 → 3). Between parts, `make -C <wt> down` the part you have
   finished — §5's "clean up nothing until the last part passes" governs *worktrees*, not *stacks*, and
   a stack left up per part is pure cost once that part is green.
6. **`worktree-new` does not install dependencies, so the tier-0 gates cannot run in a fresh
   worktree.** `worktree-init` links `.env`/`secrets` and nothing else (`Makefile:613-616`), and
   `make wt-new`'s owned path adds nothing (`wt.sh:229` calls `worktree-new` only — the
   `Worktree setup` seam is used by the *generic fallback* path, not the owned one). Both `typecheck`
   and `test-js` declare `_require-tools`, which asserts `frontend/node_modules/.bin/prettier`
   (`Makefile:698-699`). Verified failure in a worktree created exactly as §0/§2 prescribe:
   `host lint toolchain missing — run 'make tools'` / `make: *** [Makefile:699: _require-tools] Error 1`,
   exit 2. Fix: `make -C <wt> tools` as part of §0's one-time init (`make setup` also works and adds
   hooks + capacity).
7. **Running a `RUN_VITE` target before the first `make up` POISONS the worktree's vite image.** The
   vite service takes `HOST_UID` as a **build arg** (`compose.local.yaml:101`) and as its runtime
   `user:` (`:107`), both defaulting to `1001`. `HOST_UID=1000`/`HOST_GID=1000` live in
   `docker/.capacity.generated.env`, written by `_capacity-fresh` — a prerequisite of `up`, `up-built`,
   `start-built` and the marker targets, but **not** of `vite-build` or `generate-types`. Run
   `vite-build` first in a fresh worktree and the whole chain fires:
   - compose builds the vite image with `HOST_UID=1001`, so the image's `/app/node_modules` is owned
     `1001:1001` (verified: `ls -ldn /app/node_modules` → `1001 1001`, vs `1000 1000` in a worktree
     initialized in the right order);
   - the run fails `EACCES: permission denied, mkdir '/app/backend/static/dist'` — the host
     `backend/static` is `1000:1000` mode 775 and the container is uid 1001;
   - and because the bad image is **cached under the project's own tag**, generating the env file
     afterwards does **not** recover: every later `compose run vite` — `vite-build` *and* the tier-2
     `generate-types` — then fails
     `[ERR_PNPM_VERIFY_DEPS_BEFORE_RUN] EACCES … '/app/node_modules/.pnpm-workspace-state-v1.json…'`
     with the useless hint `Run "pnpm install"`.
   - **Recovery is `make -C <wt> build`** (it declares `_capacity-fresh`, so the rebuild picks up
     `HOST_UID=1000`; verified: ownership flipped back to `1000:1000` and both targets then passed
     first try). `make capacity` alone is **not** enough — it fixes the env but not the cached image.
   - Prevention is §0's init order: one `make up d=1` before any container gate.

### 6e. Verified-facts ledger

Each line was checked against the tracked file named; a bold **Correction** marks a plan assumption
that did not survive.

1. Every target cited in §6a-§6b exists in `app-main/Makefile` (`.PHONY`, line 169): `test-js`,
   `typecheck`, `vite-build`, `audit`, `audit-endpoints`, `generate-types`, `test-marker-parallel`,
   `test-marker-parallel-built`, `test-backup-pipeline`, `up`, `worktree-new`, `worktree-rm`.
2. **Correction.** `make audit` is `EXEC_WEB = $(COMPOSE) exec web bash -c` (`Makefile:124`, `:531`)
   and `make generate-types` is `EXEC_WEB_AS_HOST` + `RUN_VITE` (`:142`, `:159`, `:508`). Neither is
   host-only: both need the dev stack **up**. `test-marker-parallel` is likewise `EXEC_WEB` (`:446`).
   Host-only is `typecheck` (`:503`) and `test-js` (`:334`); container-but-stackless is `vite-build`
   (`RUN_VITE` = `compose run --rm --no-deps vite`, `:464`) and `test-backup-pipeline` (`:341`).
3. **Correction.** `tests/integration/system/test_metrics_domain_pipeline_e2e.py:28` and
   `test_metrics_domain_no_pii.py:23` are both `pytestmark = pytest.mark.cli`. The plan's backend gate
   (`members`, `mobile_api`, `unit`) could not have run them; `m=cli` is required. `pytest.ini` has no
   `system` marker.
4. **Correction (gap).** `CI.yml` runs `registry-staleness` → **Endpoint Registry Audit**
   (`flask endpoints audit --strict`). The plan's backend gate omitted its local twin
   `make audit-endpoints` (`Makefile:521`), so a backend part that adds or renames a route could pass
   every listed local gate and fail CI.
5. **Correction (naming).** There is no top-level `BackupPipelineTest` check. `BackupPipelineTest` is a
   **job key** in `test-build-image.yml:46`; its display name is **Backup Pipeline E2E**, and `CI.yml`
   calls the workflow as `build-image-test` / **Test Build Docker Container**. It runs
   `docker/backup-pipeline-test.sh app-prod:test app-workflow:test` (`:79-82`); the local target runs
   the same script with `app-local-web:test` from `docker/Dockerfile.Local` (`Makefile:341-345`) —
   confirming the plan's parenthetical.
6. **Confirmed.** `lint.yml`'s `Lint` job (display name `Code Quality`) ends with `make typecheck`
   (`lint.yml:38-39`), and in `CI.yml` the jobs `test`, `types-staleness`, `event-coverage`,
   `registry-staleness` and `build-image-test` **all** declare `needs: 'lint'`, while `lint` declares
   `needs: 'format'`. `tsc --noEmit` is not in `Tests-UI`. The plan's Step 3 correction holds, and is
   stronger than stated: typecheck gates the entire matrix.
7. **Confirmed, and it settles the plan's open question about the removal form.**
   The stronghold dispatcher `.claude/scripts/wt.sh`'s `cmd_rm` resolves the worktree **by branch**
   (`_wt_find_branch`, not by re-deriving a slug), requires the path to be under
   `<primary>/.claude/worktrees/`, and then runs `make -C <path> worktree-rm` (`wt.sh:402-431`). So
   `make -C ~/code wt-rm REPO=app-main BRANCH=split/<topic>-verify-<part>` works on a worktree created
   with the repo's own `worktree-new`. app-main's `worktree-rm` (`Makefile:635-657`)
   runs `compose … down -v --rmi local --remove-orphans`, drops the worktree's dev DB, then
   `git -C <primary> worktree remove <dir>` — and prints `(branch kept)`, so the branch is a separate
   deletion. This is why a bare `git worktree remove` is the wrong cleanup after tier-2/3 gates.
   **Two corrections found when this was actually exercised** (see `invariant-checks.md` §2):
   `worktree-new`'s `name=` is **not** an arbitrary slug — it must normalize to the branch's own slug
   (`name=split-pr749-verify-p1 b=split/pr749-verify-p1`), or the target refuses; and when `BASE`
   predates the repo's worktree targets, `worktree-new`'s `worktree-init` half fails and `wt-rm`
   **also** fails (it delegates to the repo's `worktree-rm`, absent at that `BASE`) — there, a bare
   `git worktree remove` is the *only* form that works, and it is safe because no stack was ever
   initialized.
8. **Confirmed.** `lib/branch_guard.py` `judge_worktree()` denies **only** `git worktree add`
   (first positional must be `add`), so `git worktree remove` and `git worktree list` are not denied;
   `judge_branch()` returns no item for delete-mode flags (`BRANCH_MODE_SHORT = set("larrdDmMu")`), so
   `git branch -D` is not *denied* — and `allow-git-ops.sh:89`'s destructive carve-out only makes it
   **defer**, which hands it to `Bash(git branch:*)` and **allows it prompt-free** (this file's earlier
   "defers to a confirmation prompt" reading was wrong; verified live in plan 9d — no prompt appeared
   for a two-branch `-D`). `ExitWorktree` appears in no hook or settings
   rule, and is moot because `block-branch-switching.sh:34` denies `EnterWorktree`.
9. **Confirmed.** `pytest.ini` declares the markers used here: `members`, `mobile_api`, `unit`, `cli`,
   `members_ui`, `urls_ui`. `tests/functional/urls_ui/test_multi_select_ui.py` is under `urls_ui`, so
   `m=urls_ui` is the right marker for the multi-select drive-by fix.
10. **Confirmed.** `test.yml` `Tests-UI` has exactly **13** matrix legs (`splash_ui`, `home_ui`,
    `utubs_ui`, `members_ui`, `urls_ui`, `create_urls_ui`, `update_urls_ui`, `tags_ui`, `mobile_ui`,
    `metrics_ui`, `settings_ui`, `search_ui`, `admin_ui`) and `Tests-Integration` 10. The UI legs build
    frontend assets before running (`test.yml:300`), which is why their local twin is the `-built`
    form.
11. **Confirmed.** `frontend/test-setup.ts` hand-mocks `routes`, `constants` and `strings` and injects
    them as `#app-config`; `frontend/lib/config.ts:25` declares `interface AppRoutes`, `:116` declares
    `AppConfig` with `strings: Readonly<Record<string, string>>`, and `:130` types the raw config's
    `routes` as `Record<string, string>`. Both halves of §6d-1 follow from those three lines.
12. **Confirmed.** `test-js-built` is a pure alias of `test-js` (`Makefile:337`) — the single exception
    to "`-built` ⇒ rebuilt stack". `_require-n-fits` is a prerequisite of both marker-parallel targets
    (`:446`, `:449`), so an explicit `n=` can be refused.

*Entries 13-16 come from the first end-to-end execution of this step (plan 9c, 10-08-2026), against a
synthetic 3-part branch cut from current `origin/main` — the first time these gates were actually run
rather than read.*

13. **Correction (the big one).** "Prove each part green **in the worktree Skill Step 3 built for it**"
    is wrong for every part after the first, and contradicts SKILL.md's own invariant 2. Step 3's
    worktrees hold `BASE + pk`; CI will see `BASE + p1 + … + pk`. Reproduced: a one-line frontend part
    (`type X = SuccessResponse<"splitFixtureProbe">`) failed `make typecheck` at `BASE` with
    `frontend/navbar.ts(11,57): error TS2344: Type '"splitFixtureProbe"' does not satisfy the
    constraint 'keyof operations'` (exit 2), and passed after merging the backend part in. §0 now
    requires the prefix merge.
14. **Correction (blocking).** A verification worktree has no `frontend/node_modules`, so the first two
    gates of every §6b sequence die on `_require-tools`. See §6d-6. §0 now requires `make tools`.
15. **Correction (blocking, and destructive).** `vite-build`'s tier-1 "no running stack" label is only
    true after one `make up`; run first in a fresh worktree it caches a `HOST_UID=1001` vite image that
    breaks every later `RUN_VITE` target until `make build` rebuilds it. See §6d-7. §1's ordering rule 0
    and §0's init now cover it.
16. **Gap closed.** The repo ships `make affected-markers` / `make test-affected`
    (`scripts/affected_markers.py`), a per-file marker derivation this file never mentioned while
    hand-listing marker sets in §6b. It independently reproduced the §6d-2 trap (`docs/endpoints/*` →
    `cli, unit`) and flags **broad** files (`backend/utils/all_routes.py`, `frontend/types/*`) that
    escalate a part to `Integration: everything` / `UI: everything`. See §3. It also prints a
    standing warning when a route file changed without the registry being regenerated
    (`affected_markers.py:880-887`) — a free atom-violation detector for a backend part that split its
    route from `docs/endpoints/`.
17. **Confirmed, with a refinement.** `make wt-rm REPO=<repo-dir> BRANCH=<verify-branch>` is the right
    cleanup form for a worktree created by `worktree-new` **at a current `BASE`** — all five of plan
    9c's worktrees (3 parts + union + the fixture) were removed with it, each tearing down the stack,
    dropping the dev DB and printing `(branch kept)`; `git branch -D` then took all five branches in
    one call. §6e-7's stale-`BASE` caveat (where `wt-rm` fails and a bare `git worktree remove` is the
    only form that works) is specific to a `BASE` predating the worktree targets, not the normal case.
