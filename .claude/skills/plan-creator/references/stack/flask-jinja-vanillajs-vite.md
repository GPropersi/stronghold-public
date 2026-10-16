# Stack Protocols — flask-jinja-vanillajs-vite

`Applies when Stack intersects: flask, jinja, vanillajs, vite, python, sqlalchemy, pydantic, playwright, vitest, pip`

These are the genuinely stack-bound plan-creator protocols, moved out of the central `SKILL.md`. Load and
apply them **only** when the working repo's `## Claude Config` → **Stack** value intersects any tag in the
header line above. They capture the original app-main/Flask behavior verbatim in substance, so a repo whose
Stack is `flask-jinja-vanillajs-vite` gets exactly the plan-creator behavior it had before genericization.

The central SKILL.md keeps the stack-neutral *principle* for each of these (e.g. "match the real app's
colors", "pin dependencies", "one requirement at a time", "verify CSRF token rendering"); the concrete
tooling and vocabulary live here.

---

## Package Pinning (pip / `requirements-*.txt`)

Any plan step that adds a new Python package **must**:

1. Pin it to the **latest stable version** (check PyPI: write a temp script to `$TMPDIR/check_version.py`
   using the `Write` tool, then run `curl -s https://pypi.org/pypi/<package>/json | python3 $TMPDIR/check_version.py`
   — NEVER use inline `python3 -c` with braces)
2. If stable is incompatible with the existing stack, use the latest working version instead — document why
   in the to-do item.
3. Pin **all transitive dependencies** introduced by the new package to exact versions as well. Run
   `pip install <package>==<version>` in the container, then `pip show <package>` and inspect the
   `Requires:` field. Add each unlisted dependency at its installed version.

**When using a new feature of an already-pinned package** (e.g., `EmailStr` requires `email-validator >= 2.0`,
a new API method requires a minimum version), check the current pin in `requirements-prod.txt` before writing
any to-do that depends on that feature. If the current pin is too old, add a pre-work to-do to update it —
with a `make build` and container-level smoke test — before any to-do that uses the feature. Do not assume
the existing pin is compatible.

**Always use `==` (not `>=`, `~=`, or ranges).** Add the package and its transitive deps to the appropriate
requirements file based on usage:

| File | Use when |
|---|---|
| `requirements-prod.txt` | Needed at runtime in production |
| `requirements-test.txt` | Only needed for running tests (includes prod via `-r`) |
| `requirements-dev.txt` | Only needed for local dev tooling / pre-commit (includes test via `-r`) |

---

## TDD Enforcement — implementation standards (Flask / Vitest / Playwright)

The stack-neutral Red → Green → Refactor loop stays in the central SKILL.md. These are the stack-specific
implementation standards that fill it in for this stack:

- **Backend (Flask):** Prioritize integration or unit tests. Match existing style and directory structure in
  `tests/`.
- **Frontend – logic/state:** Use Vitest with JSDOM (fast, isolated).
- **Frontend – navigation/critical flows:** Use Playwright (end-to-end verification).
- **Philosophy:** Tests are the contract; code is the fulfillment. Never write tests to fit existing feature
  code.

---

## UI Mockup fidelity — match app-main / Bootstrap

The central SKILL.md keeps the general fidelity principle ("match the real app; pull real values from the
codebase; never invent colors"). For this stack, apply the concrete guidance:

- The mock must look like **APP-MAIN**, with the **correct colors and styling**.
- Grep the project styles for real values: CSS custom properties / theme variables, `frontend/**/*.css`,
  **Bootstrap utility classes already in use**, and existing Jinja template markup. Every hex color, font
  family, spacing, border-radius, and class name in the mock must trace to a real value used by the app.
- Reuse existing **Bootstrap class names** and the Jinja template's DOM structure where practical, so the
  mock reads like the real rendered page and the implementer can map it directly to templates.

---

## CSRF / Auth / Session specifics (Jinja token rendering)

When a plan touches form submission, authentication, or CSRF:

1. **Token rendering:** Read the Jinja template. Note any `{% if ... %}` guards. Explicitly state whether the
   condition is satisfied for all relevant user states (authenticated, unauthenticated, unvalidated). If not,
   add a to-do to fix the guard.
2. **Test token acquisition:** Read the fixture. State exactly which HTML element the fixture parses to get
   the CSRF token, and verify that element will exist in the response after the template changes.
3. **Frontend token consumption:** Read the JS. Confirm the DOM element it targets (`meta[name=csrf-token]`,
   hidden input, etc.) is present in the rendered page for all user states the form is shown to.
