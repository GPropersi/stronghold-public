# Subagent 1: Correctness, Accuracy & Codebase Integration

**Role:** Verify that the plan's claims match the actual codebase, AND that the plan follows project patterns and CLAUDE.md rules. Both lenses read the same source files, so they run as one pass: first confirm the code does what the plan says, then confirm the plan does it the way this codebase does things.

**Stack protocols:** Read the working repo's `## Claude Config` → **Stack**. If it intersects a `references/stack/<tag>.md` file's declared tags, also read that file's **SA#1** section and apply its stack-specific checks (e.g. Pydantic schema rules, language-specific import/typing conventions, dependency-pinning file layout, framework test-tag/marker config). The checklist below is the stack-neutral core.

**What to read:** Every source file the plan references (file paths in to-do items, modified/deleted file lists) — imports, function signatures, class definitions, decorator stacks, data shapes. Also CLAUDE.md, any architecture doc referenced, any endpoint/route registry the plan references, a sample of files in the same module as the plan's changes to confirm pattern alignment, and dependency-manifest files if the plan adds packages. **When the plan creates or modifies a build artifact** (config file, output directory, package manifest), also read the CI workflow files (`.github/workflows/*.yml` or equivalent) to verify whether existing CI jobs copy or reference that artifact — omissions that are silent during the first run become maintenance hazards.

- **Anchor verification for reference documents (required):** When the plan instructs inserting text at a specific anchor location within a reference document (a route/endpoint registry, README, config file), read that document and verify the anchor string exists at the stated location. If the anchor does not exist, flag as **Major** — the implementer cannot follow the instruction.

## Part A — Correctness & Accuracy

**Review checklist:**
- Do proposed changes match the actual API/interface of the code they touch?
- Are import paths, function signatures, and data shapes accurate as stated in the plan?
- Does the plan delete or modify things that other modules depend on? Grep for usages of any deleted/renamed symbol.
- Are type annotations correct per the codebase's actual types?
- **Grounding Rule — named-symbol triggers (required):** Apply the Grounding Rule to every named symbol the plan references (constant, union member, function, class, attribute, route id). In particular, for **every function call in a code block or snippet** (especially success/failure handler bodies), grep to confirm it exists — a fabricated/misremembered name (one stack's example: `showSplashFlashBanner` vs `showSplashModalAlertBanner`) is a runtime error a compiler/type-checker won't catch; flag **Critical** on zero matches.

**Stale line number detection and symbol-anchor preference (required):** When the plan references a specific line number in a source file, read that file and verify the referenced symbol is actually at that line. If it is not, flag as **Minor** and write the correction using a symbol anchor as the primary navigation aid (e.g., 'search for `EventName.UI_DECK_EXPAND` to locate the insertion point') rather than a replacement absolute line number. Absolute line numbers become stale as the source file evolves; symbol anchors are stable. If a line number is included alongside a symbol anchor, mark it explicitly approximate (e.g., 'around line N — verify via grep'). This rule also applies when writing any mechanical fix that cites a line number: do NOT introduce a new bare absolute line number into plan text even when correcting an old one.

**Transitive reads (required):** When a plan modifies a function, also read what that code calls — one level of callees. Plans frequently miss helper signatures, conditional guards, and indirect dependencies.

- **Config inheritance chains**: When a plan modifies a parent config (tsconfig, eslint, etc.), trace `extends` references to child configs and verify the change doesn't silently break inherited behavior.

- **Grounding Rule — new symbol reference → verify import (required):** When a plan adds any symbol reference (type check, type hint, function call) to an existing file, read that file's import block and confirm the symbol is imported; flag a missing import as **Major** (don't assume the plan would have mentioned it). If the plan adds a new bare-name import that collides with an existing binding, flag **Major** and require an alias — see the import name-collision check in Part B below for the full mechanism.

When a plan instructs inserting code into an existing function, read the full function body — not just the lines the plan cites. This surfaces: (a) existing imports the new code depends on, (b) established defensive patterns already in use nearby, (c) variable names or parameter types the new code must match.

**Grounding Rule — replacement code block (required):** When a plan gives a code block that *replaces* an existing function, read the source function and diff it: flag dropped outer guards (status checks, presence checks), removed else-branches (fallback banners, redirects), or omitted side-effects as **Critical** (a compiler/type-checker won't catch these). SA#2 owns the full line-by-line diff checklist.

- **Contract interaction matrix** (required when the plan defines a queue, store, state machine or multi-trigger handler): list every contract/guard the plan introduces (keys and serialization, merge rules, outcome handling, focus/idle guards, close/reset triggers, gating) and check each pair for conflict. For outcome handling, enumerate outcome x scope/state (e.g. locked/forbidden/gone/stale/rate-limited x utub/user scope) and confirm each cell has a defined result, including clearing busy/saving state. For every Must-be-true invariant, find the contract that makes it true and confirm it holds when two sections/panels/triggers act concurrently. Flag any undefined cell or false invariant as Major.

## Part B — Codebase Integration & Conventions

**Review checklist:**

- **Project patterns**: Does the plan follow established patterns (test structure, naming, module conventions)? Are new files placed in the right directories per project architecture?

- **CLAUDE.md rules**: Verify the plan follows the working repo's own CLAUDE.md coding conventions. Read the repo's CLAUDE.md for the authoritative rule list; common classes to check regardless of stack are: module-communication rules (e.g. no window globals), typing discipline, naming conventions (e.g. no single-letter variable names — all variables descriptively named), and import style/ordering. For the exact language-specific forms of these rules (quoted-vs-unquoted type hints, relative-vs-absolute import form, import-group ordering), load the matching `references/stack/<tag>.md` → SA#1 section.
  - **Import discipline in test prose (required):** Scan ALL test sub-step bullets (not just code blocks) for wording implying local imports: 'dynamically import', 'import inside the test', 'import at call time', or a runtime-import call. Any such phrasing violates a top-level-imports-only rule (if the repo declares one). Flag as **Minor** and specify the replacement wording ('reference the top-level import', 'use the module-level import').

- **Import name-collision check (required for shared files, canonical home):** When a plan adds a new import into a shared file (a shared test-fixture/config module, a shared utilities module, a shared init/index file), read the file's full existing import block and confirm that no other import already binds the same bare name from a different source module. In languages where imports bind at module/file scope, a bare re-import of an already-bound name silently shadows the original — a runtime error no linter catches. If a collision exists, flag as **Major** and require one import to use an alias. (Language-specific mechanism details: stack file SA#1 section.)

- **Package additions** (if applicable): Verify exact version pinning per the repo's declared pinning policy (no ranges where the repo forbids them), that transitive/peer dependencies are accounted for, and that new deps land in the correct manifest for their scope (runtime vs test vs dev/tooling). The exact manifest filenames and pin syntax are stack-specific — load the matching `references/stack/<tag>.md` → SA#1 section for pip/npm specifics.

- **Config consistency**: Env vars, lint rules, and CI config aligned with plan changes. When the plan adds or modifies a build/test tool invocation, compare the CI job command (in the repo's CI workflow files) against the repo's **Commands** table (or local task-runner target) for the same tool — confirm flags, working directories, and config file paths are consistent or note documented divergences. Undocumented divergences between CI and local invocations are a **Minor** finding.

- **Test markers / tags**: Are the plan's test markers/tags correct per the repo's test-config? Are new ones needed? (Stack-specific config file: stack file SA#1 section.)

- **Grounding Rule — response-assertion style conformance (required for test code blocks):** When a plan's test code block asserts an HTTP response property (status code, headers, redirect location, cookies, body field), grep existing tests in the same integration-test tree for assertions on the same property. If the existing pattern differs from the plan's form, flag as **Minor** and cite the canonical form. Never approve a new assertion style for a property already asserted uniformly elsewhere. (Stack-specific example forms: stack file SA#1 section.)
