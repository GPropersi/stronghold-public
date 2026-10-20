# Subagent 3: Ordering, Dependencies, Cleanup & Completeness/Risk

**Role:** Verify step sequencing, intermediate state safety, and cleanup correctness, AND identify gaps, risks, and underspecified steps. Both lenses are about what the plan *fails to account for* — one temporally (what's missing between steps), one substantively (what's missing within a step) — so they run as one pass.

**Stack protocols:** This subagent's checklist is stack-neutral. The code examples below (Python imports, JS lint rules, config filenames) are labeled as **one stack's example** — apply the underlying principle to whatever stack the working repo declares. If the repo's `## Claude Config` → **Stack** intersects a `references/stack/<tag>.md` file and you need the exact linter-rule names or manifest filenames, consult that file, but there is no dedicated SA#3 stack section.

**What to read:** The plan in full, plus lint/tooling config files (whatever the repo uses — e.g. `tox.ini`, `.flake8`, `pyproject.toml`, `.eslintrc`), any files where the plan defers deletion or cleanup to a later step, and any files where the plan's specificity claims can be checked (schema definitions, factory methods, template structures).

## Part A — Ordering, Dependencies & Cleanup

**Review checklist:**

- **Step sequencing**: Are steps ordered so each prerequisite is done before its consumer? Would executing steps out of order cause failures?

- **Intra-step bullet ordering (required):** Within each step, read the sub-bullets in top-to-bottom order and verify that every symbol, constant, or file referenced in a code block is defined or created by an earlier bullet in the same step. (One stack's example: a code block that uses `M.SEARCH_RESULTS` before the bullet that adds `SEARCH_RESULTS` to `model_strs.py` is an ordering error even though both bullets are in the same step.) Flag as **Minor** if an implementer following the bullets top-to-bottom would encounter an undefined symbol.

- **Intermediate state (required)**: For every step, verify the application is fully working after that step completes — not just at the end of the plan. Any gap where an endpoint is silently broken between steps is a **Major** finding, even if integration tests pass.

- **Deferred cleanup commit gate (required)**: For any symbol (import, function, export, variable) that becomes unused partway through but is only removed later, verify lint config does not enforce a rule blocking the intermediate commit. Common blockers (one stack's examples): F401 (unused Python import), `no-unused-vars` (JS). If active, cleanup must move into the same step that makes the symbol unused.

- **Dead import analysis (required for every deletion)**: When a step deletes a function/class/helper or changes a return expression, perform a full transitive import scan:
  1. List every symbol the deleted code uses from imports
  2. Grep the file for remaining usages excluding the deleted code
  3. If no other usage exists, that import is dead and must be removed in the **same step**
  Common misses (one stack's examples): `typing` imports used only in deleted helpers, `render_template` whose sole call is deleted, utility functions called only by deleted code, string constants referenced only in deleted branches.

- **Forward references**: No to-do item should reference a function/class that doesn't exist yet and isn't created in the same or earlier step.

- **Framework lookup / discovery claims (required):** When a plan makes a factual claim about how a framework resolves or discovers resources — test-fixture scoping and config-file hierarchy, route/blueprint registration order, ORM relationship traversal, module bundler resolution — verify the claim against the framework's documented behaviour rather than accepting the plan's prose. A plan that simultaneously offers two contradictory resolution mechanisms (e.g. "replicated into the shared fixture file OR construct inline" AND describes that file as "empty") is internally contradictory and signals an unverified assumption. Flag as **Major** if the plan's stated resolution rule is inconsistent with the framework's actual mechanism.

## Part B — Completeness, Risk & Specificity

**Review checklist:**

- **Edge cases & completeness**: What scenarios are not covered (empty state, error paths, boundary conditions)? Race conditions, async timing issues, or state management gaps?

- **Implementation specificity**: For every step that defines or modifies a class, function, schema, or template:
  - Does the plan provide enough detail (exact field definitions, method bodies, variable extraction instructions, explicit code patterns) for an implementer to execute without additional research?
  - Flag any step where a developer would need to "figure out" details the plan leaves unstated (factory method bodies, full alias declarations, explicit variable extraction, required refactors). These are **Major** findings.
  - Partial specifications are not acceptable: if a schema has five fields and only one has an alias/annotation declared, the other four are underspecified even if "derivable."
  - **Variable-binding audit for rewrites and ports (required):** When a plan provides a code body for a function described as a 'rewrite', 'port', or 'analog' of a named existing function, read the original function in the source file and audit the plan's body: every name used in the plan body (as an argument, in an interpolation/f-string, in a keyword argument, or in a subscript) must be either (a) a parameter of the new function, (b) assigned explicitly earlier in the body, or (c) a module-level name (import or constant). Any name used in the plan body that was bound in the original via an assignment the plan dropped is an undefined-variable bug. Flag as **Major** and cite the missing binding.
  - **Success handler body spec completeness (required for frontend plans):** For every AJAX success handler a step covers, verify that the plan provides either: (a) a complete body spec, or (b) an explicit note that the body requires no changes. A success handler with side effects beyond the typed variable assignment (e.g., a modal `.hide()` call, a navigation redirect, a status guard, a secondary helper call) that is covered only by a type-signature bullet is underspecified. Flag as **Minor** if the omitted behavior could be dropped by an implementer following only the plan text.
  - **Config file option coverage (required when a plan proposes a new config file):** For every option listed in a proposed config file (tsconfig.json, vite.config.js, pyproject.toml, etc.), verify that the plan's rationale section explains WHY that option is present. Any option with no rationale bullet is a **Minor** finding — either the rationale must be added or the option must be removed.

- **Risk & reversibility**: Which steps are hard to undo (file deletions, DB migrations, API contract changes)? Steps that could break CI or affect shared infrastructure?

- **Cleanup**: Does the plan handle cleanup of temp files, test fixtures, orphaned imports?

- **Grounding Rule — pre-existing path collision**: When a plan says "Create directory/file X", read the filesystem to check if X already exists. If it does, flag that the plan should acknowledge pre-existing contents and specify merge/overwrite behavior.

- **Config file scope correctness (required when a plan creates a shared config file):** When a plan proposes a new tsconfig.json, jest/vitest config, or similar config that applies to all source files, check whether any types, globals, plugins, or include entries expose test-only APIs (e.g., test-runner globals, test type packages) to production code. If so, flag the missing scope boundary (e.g., a separate test-scoped config) as **Minor** and note the trade-off.

- **Breaking changes**: API contracts, shared state, DB schema, cross-module dependencies — does the plan account for all consumers?

- **Grounding Rule — single-instance conversion completeness (required):** When a plan fixes only one known instance of a broader pattern (e.g., 'convert this one raw int to an enum'), grep the codebase for all call sites of the same pattern and verify no other instances exist. If others are found, flag as **Major** — the plan must either convert all instances or explicitly acknowledge the remaining ones as intentional.

- **Fragility-pattern sweep (required when reviewing shell scripts / smoke-test files):** When a finding identifies a *fragility pattern* in a specific location — a flat `sleep N` settle, an unquoted variable, a missing readiness wait, a bare container-exec without a quoting guard — scan the same file for all other occurrences of that identical pattern. If the plan's fix resolves only the named occurrence while sibling occurrences in the same file share the same fragility, flag as **Major** and require the fix to sweep all siblings. This rule fires regardless of whether the siblings were named in the original finding.

- **Stated premises about existing code**: For every claim the plan makes about existing markup, CSS or runtime state (a class on an element, a z-index/position value, a computed style, an event binding style, a precedent such as 'X already does Y'), open the source file and confirm it, citing file:line in the finding. Pay particular attention to premises that a test asserts on (e.g. a computed z-index comparison): an unconfirmed premise makes the test itself wrong. Flag a false premise as Major.
