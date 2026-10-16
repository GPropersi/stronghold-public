# Stack reviewer heuristics — flask / jinja / vanilla-JS / Vite

Applies when Stack intersects: flask, jinja, vanillajs, vite, python, sqlalchemy, pydantic, playwright, vitest, pip

These are the genuinely stack-bound PR-review heuristics for a Flask + Jinja + vanilla-JS→Vite/ES6 codebase (app-main's stack). A subagent loads this file when the working repo's `## Claude Config` → **Stack** intersects the tags above, and folds the section matching its role into its checklist. Filenames/registries below (`ENDPOINT_REGISTRY.md` — now generated under `docs/endpoints/` from `endpoint-registry.json`, never hand-edited; `ARCHITECTURE.md`, `APP_CONFIG.strings`, Alembic) are this stack's/repo's concrete artifacts — confirm they exist in the working repo before flagging against them.

## `fix_type` classification — stack-specific `mechanical` examples

Add these to the mechanical list:
- Naming: quoted type hints (ruff UP037) — enforced by make lint in app-main
- Debug artifacts: `window.*` global exposures
- Regenerated `docs/endpoints/` committed for an endpoint added/changed/removed in the diff (`make generate-endpoints`)
- Replacing hardcoded display strings with `APP_CONFIG.strings.KEY` lookups (the string bridge already exists)

## Subagent 2: Correctness — stack additions

- Error handling: missing `is429Handled(xhr)` guard in `.fail()` handlers (rate-limit handling)

## Subagent 3: Code Patterns — full idiom checklist

Review the diff for adherence to these established patterns (codified in `CLAUDE.md`):

Frontend:
- **Event bus** for module communication (no `window.*` globals)
- **ajaxCall()** wrapper for all AJAX (never raw `$.ajax`)
- **Schema<> / SuccessResponse<>** type helpers for typed AJAX
- **Type-guard dispatch** (`const FIELDS = [...] as const` + `isFieldName()`) for field-level validation errors
- **is429Handled(xhr)** at top of every `.fail()` handler
- **offAndOnExact** for rebinding listeners on repeatedly-shown elements
- **APP_CONFIG.strings.KEY** for all user-facing strings (no hardcoded display strings)
- **Destructured object params** for 2+ params or a bare boolean (`app/destructured-params`) — enforced by make lint in app-main
- **App store** (`getState()` / `setState()`) for shared state

Backend:
- **Pydantic schemas** for request/response (no raw dicts at boundaries)
- **Unquoted type hints** (ruff UP037) — enforced by make lint in app-main
- **`@model_validator` vs `@field_validator`** chosen correctly
- **Service layer** separation (routes call services, services handle DB)
- **Absolute imports only** (`from backend.x import y`)

## Subagent 5: Test Coverage — stack additions

- Frontend changes without UI test coverage (`*_ui` markers) — integration tests do not exercise the browser
- Backend changes without integration tests (real test DB and/or Redis, not a mock)

## Subagent 7: Consistency & Style — stack-specific rules

- Top-level imports only (no local imports inside functions — exception: `vi.importActual()` in vitest `it()` blocks)
- Import ordering (stdlib → third-party → project, ruff I001) — enforced by make lint in app-main
- No quoted type hints (ruff UP037) — enforced by make lint in app-main
- No `assert foo is True` / `is False` — use `assert foo` / `assert not foo`
- No `console.log/warn/error` left in shipped TypeScript
- `performance.now()` for in-page timing; `Date.now()` only for cross-page persistence
- Device type via shared int enum, never raw strings

## Subagent 8: Documentation & Registry — stack/repo artifacts

- **Endpoint registry (`docs/endpoints/`)**: the generated registry is enforced by the `Endpoint Registry Audit` CI job (`make audit-endpoints` locally), so don't hand-check rows — but a hand edit to `docs/endpoints/` is a finding (regenerate with `make generate-endpoints` instead). An `@api_route` needs JS linkage through one of four channels in `backend/utils/all_routes.py`: a `JS_ROUTES`/`ADMIN_JS_ROUTES` key, a template `url_for`, an `INDIRECT_JS_ENDPOINTS` entry (JS calls it via the page's own URL or a server-built URL), or a `NO_JS_ENDPOINTS` reason (genuinely no web-JS caller). Flag a new `@api_route` that was satisfied by a `NO_JS_ENDPOINTS` entry when it actually has a JS caller (that belongs in a route key, a template `url_for`, or `INDIRECT_JS_ENDPOINTS`).
- **`ARCHITECTURE.md`**: significant structural changes (new blueprint, new module layer, new extension) need an architecture entry.
- **Migrations**: model column changes need an Alembic migration in the diff.
- **README/setup docs**: new env vars, new make targets, new dependencies.
