# Stack protocols — Flask / Jinja / vanilla-JS → Vite

Applies when Stack intersects: flask, jinja, vanillajs, vite, python, sqlalchemy, pydantic, playwright, vitest, pip

These are the **stack-bound** reviewer checklists moved out of the stack-neutral SA reference files. A reviewer loads this file only when the working repo's `## Claude Config` → **Stack** intersects the tags above (see `common-response-format.md` → "Stack-specific protocols"). Each section augments the correspondingly-numbered SA reference file's stack-neutral checklist. If the repo's Stack does not intersect these tags, ignore this file entirely.

---

## SA#1 — Correctness, Accuracy & Codebase Integration (stack additions)

**Pydantic specifics (apply whenever the plan defines or modifies Pydantic schemas):**
- **Cross-field validators**: if a validator reads `info.data.get("field")` and that field has its own validation, verify the guard `if "field" not in info.data: return value` is present. Pydantic v2 omits failed fields from `info.data`, so without the guard a spurious second error fires.
- **`from_attributes=True` factories**: verify that ORM attribute names match schema *field names* (not aliases). If they differ, `model_validate(orm_obj)` raises `AttributeError`.
- **Alias completeness**: if any field in a schema uses `Field(alias=...)`, ALL fields must have explicit alias declarations. Partial alias specs cause silent `model_validate` failures.
- **Error-handling pattern**: `try/except ValidationError -> log` swallows schema drift. Schema validation calls must be bare (no try/except) so `ValidationError` propagates as a 500.

**Language-specific CLAUDE.md rules (Part B — Python/TS conventions):**
- **Type hints**: use typehints — no shortcuts. No quoted type hints (ruff UP037) — enforced by make lint in app-main.
- **No relative imports** — always use absolute project paths like `from backend.schemas.requests._sanitize import ...`.
- **Import ordering** (stdlib → third-party → project, ruff I001) — enforced by make lint in app-main.
- **No window globals** for module communication (frontend).

**Import name-collision check (module-scope rebind, canonical home):** When a plan adds a new import into a shared file (`conftest.py`, a shared utilities module, a shared init file), read the file's full existing import block and confirm no other import already binds the same bare name from a different source module. In Python a bare re-import of an already-bound name silently shadows the original at module scope — a runtime error no linter catches. If a collision exists, flag as **Major** and require one import to use an alias.

**Package additions (pip / npm pinning):**
- Use exact pin (`==`) not ranges (`>=`, `~=`, `<`). Pin transitive dependencies.
- Place in the correct requirements file: runtime in `requirements-prod.txt`, test-only in `requirements-test.txt`, dev/tooling in `requirements-dev.txt`.
- **Peer dependency verification**: when a plan adds packages that depend on a peer (e.g., `@typescript-eslint/parser` requires `eslint`), read the actual `package.json` to confirm the peer is present. Never assume from plan text.
- **TypeScript types sub-path validation (JS/TS plans):** when a plan adds an entry to a tsconfig `types` array (e.g., "vitest/globals", "@testing-library/jest-dom"), verify the sub-path is a documented TypeScript types entry point for the pinned version. Flag as **Minor** if unverifiable at review time, with a suggested runtime verification command.

**Test markers**: are markers correct per `pytest.ini`? Are new markers needed?

**Response-assertion style conformance (test code blocks):** When a plan's test code block asserts an HTTP response property (status code, headers, redirect location, cookies, body field), grep existing tests in the same `tests/integration/` tree for assertions on the same property. If the existing pattern differs from the plan's form (e.g., existing redirect tests use `urlsplit(response.location).path` but the plan uses `response.headers['Location']`), flag as **Minor** and cite the canonical form. Never approve a new assertion style for a property already asserted uniformly elsewhere.

---

## SA#2 — Full-Stack Trace (stack additions)

**Global hook conflict check (jQuery specifics):** For any file the plan modifies that registers a global side effect — jQuery prefilters (`$.ajaxPrefilter`, `$.ajaxSetup`), global event listeners (`document.addEventListener`), window-level interceptors — grep the codebase for all other registrations of the same hook. If another file registers the same hook (and both files are loaded on the same page), flag the double-registration as **Major** and note the behavioral consequence (e.g., handler fires twice per request). Do not trust the plan's prose claim that 'X is already handled' — trace the full registration chain.

**CSRF token delivery (full Flask-WTF / Jinja trace required):**
- **Source**: Read the Jinja template that renders the token (meta tag, hidden input, cookie). Check for conditional guards (`{% if auth %}`, feature flags). Verify the condition is True for every user state hitting this endpoint (unauthenticated, authenticated-not-validated, authenticated-validated, test clients).
- **Reader**: Read the JS that extracts the token. Confirm the DOM element or cookie it reads actually exists in the rendered page for all user states.
- **Transport**: Confirm the token reaches the server (header, body, cookie) and that Flask-WTF checks that location.
- If the plan asserts "CSRF is already handled," **do not accept this at face value** — trace it yourself.

**`handleImproperFormErrors` field key alignment:** Do the keys in the service's `errors` dict match the `case` labels in the handler's switch? Those keys must match `id` attributes on `<input>` elements (camelCase). A mismatch silently discards the error.

---

## SA#5 — Verification & Test Coverage (stack additions)

**Login-fixture redundancy:** When the plan defines any explicit header dict, login fixture, or test-client helper for test code, read the definition of the test client class and login fixtures to verify the plan's setup is not redundant with what the fixture already provides automatically (e.g., `AjaxFlaskLoginClient` auto-injects `X-Requested-With: XMLHttpRequest`).

**Type-check-only verification for rename steps:** When a step's only verification is `make vite-build` + `npm run typecheck` and the step renames a file from `.js` to `.ts`, flag as **Minor** if `make test-js` is not also included. Type-checking alone cannot catch runtime errors (e.g., missed export, bad cast that throws at runtime) that vitest would catch. If the plan defers all `make test-js` runs to a later step, note the step attribution gap: a runtime regression introduced in an earlier step will be undetectable until the later `test-js` run.

**Built-target rule for UI tests (Makefile `EXEC_WEB_BUILT`):** The built-targets rule ('UI tests always use built targets — never run UI tests against the dev server') applies to EVERY `make test-*` reference in the plan — explicit verification bullets, Red-Green iteration hints, and per-test debug loops alike. When reviewing any step that mentions a `make test-*` command for UI tests, read the Makefile and confirm the target uses `$(EXEC_WEB_BUILT)` (or `start-built` as a prerequisite) rather than `$(EXEC_WEB)`. If a non-built target is referenced for UI tests in any context, flag as **Major** regardless of whether the instruction is labeled 'verification' or 'iteration'.

**Route path verification in tests (OpenAPI spec paths):** When a test example names a specific route path (e.g., `spec['paths']['/utubs/{utub_id}/urls/{utub_url_id}']`), read the corresponding route file and confirm (a) the path is registered, and (b) all HTTP methods registered at that path are known. A path claimed to be GET-only that also has PATCH/DELETE will cause assertions like 'x-key absent on this path' to fail. Flag as **Major** if the test example path has methods the plan doesn't account for.

**`vi.hoisted` mock factory shape verification (vitest `vi.mock` + `vi.hoisted`):** When a plan's test code block uses `vi.hoisted(() => ({ ... }))` to produce a factory for `vi.mock`, verify that calling the factory function returns an object whose keys match the mocked module's exported names. A bare `vi.fn()` stored as the factory and then called as `vi.mock('path', () => mockFn())` returns `undefined` — not a module-shape object — making every import from the mock `undefined` and silently voiding all assertions on those imports. The correct pattern is `vi.hoisted(async () => await import('../../__tests__/helpers/mock-metrics-client.js'))` or an inline object factory `vi.hoisted(() => ({ emit: vi.fn() }))`. Flag any bare `vi.fn()` used as a `vi.mock` factory as **Major**.
