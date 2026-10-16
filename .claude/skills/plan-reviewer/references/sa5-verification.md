# Subagent 5: Verification & Test Coverage

**Role:** Ensure each step has sufficient, layer-appropriate verification.

**Stack protocols:** Verification is expressed in terms of the working repo's **Commands** table (`## Claude Config` → Commands: Integration tests / UI tests / JS-unit tests / Build / Lint-format). Use those command names when phrasing layer-match and final-suite checks — do NOT hardcode any specific task-runner target. Read the repo's `## Claude Config` → **Stack**; if it intersects a `references/stack/<tag>.md` file's tags, also read that file's **SA#5** section for stack-specific test checks (built-target rules, mock-factory shape verification, login-fixture redundancy, rename-step runtime coverage, route-path spec verification). The checklist below is the stack-neutral core.

**What to read:** The plan's verification commands, test files referenced in the plan, the repo's test-runner config (available markers/tags and targets — from the **Commands** table and the config file it points to), and — when the plan defines any explicit header dict, login fixture, or test-client helper for test code — the definition of that helper to verify the plan's setup is not redundant with what the fixture already provides automatically (stack file SA#5 section covers the framework-specific instances).

**Review checklist:**

- **Verification exists**: Does each step have a clear way to verify success (a command to run, a behavior to observe)? Note any steps that lack verification and suggest what to add.

- **Rename-step runtime coverage (required):** When a step renames or moves a file and its only verification is a build/type-check command, flag as **Minor** if a runtime test command (the repo's **JS-unit tests** / **Integration tests** row, as appropriate to the layer) is not also included. Type-checking/building alone cannot catch runtime errors (missed export, bad cast that throws at runtime) that the runtime test suite would catch. If the plan defers all runtime test runs to a later step, note the step attribution gap: a runtime regression introduced in an earlier step will be undetectable until the later run. (Stack-specific rename form, e.g. `.js`→`.ts`: stack file SA#5 section.)

- **Layer-match check (required)**: For each verification step, confirm the test type exercises the layer the change affects, using the repo's **Commands** table vocabulary. Common mismatches to flag as **Major**:
  - Template/HTML/markup changes verified only by integration tests (`client.post()`/`client.get()`-style) — these bypass the browser; need the repo's **UI tests** or a browser driver
  - JS/client behavior changes verified only by integration tests — need the repo's **JS-unit tests** or **UI tests**
  - Backend-only changes verified only by UI tests — the repo's **Integration tests** are faster and more precise
  If a step changes templates or client JS and the only verification is the integration-test command, flag it and recommend adding the **JS-unit tests** command and/or the relevant UI marker/test.

- **Test coverage**: Happy path, sad path, and edge case tests exist for every changed endpoint? Missing edge case coverage?

- **Grounding Rule — named test helper/fixture (required):** Apply the Grounding Rule to any test helper, fixture, or utility the plan names (one stack's example: `wait_then_get_at_least_n_elements`) — grep the specified file before writing any finding or conditional fallback that depends on its presence or absence.

- **Red-phase test count consistency (required):** When a step's Red sub-phase has a verification command of the form "confirm N tests fail" (or matching Green-phase form "confirm N tests pass"), count the test-case specifications defined in that same Red block and verify the count equals N. If they differ, flag as **Critical**. When a prior review pass added a new test case to a Red block, the "confirm N tests fail/pass" number must have been updated atomically across both Red and Green phases — if it was not, the count is stale regardless of which pass introduced the test case.

- **Final test suite phase (required)**: The last phase must run the repo's full **Integration tests** and **UI tests** commands (from the **Commands** table). Flag as **Critical** if either is missing.

- **Verification sufficiency**: Are the verification steps actually sufficient to catch regressions?

- **Deferred verification gaps**: When a step's verification depends on a file that can only be deployed manually (e.g., CI workflow files), flag the gap and require a local verification alternative.

- **Failure path guidance**: Every verification command in the plan — including intermediate checks AND final test suites — must have at least one concrete failure-mode hint (likely error message + resolution). Flag any verification step that lacks this.

- **Test assertion falsifiability (required):** For each negative test assertion (e.g., `assert key not in dict`), verify that the assertion targets the exact dict/field that the implementation would populate — not a parent container. An assertion on a parent dict passes trivially if the key is only ever inserted at a child level. Flag as **Major** if a negative assertion would pass even if the implementation is broken.
