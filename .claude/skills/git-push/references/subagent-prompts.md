# Subagent Review Prompts

Each subagent receives the diff output and a focused review prompt. All subagents must return a structured JSON response.

Reviewers should ground project-specific conventions (naming, type hints, imports, module-communication patterns, registry/consistency invariants) in the **working repo's `CLAUDE.md`** rather than any single stack's rules. The examples below are illustrative; apply the working repo's actual conventions.

## Response Format (all subagents)

**File delivery:** Write your complete JSON response to the file path provided in your prompt (`<tmp-dir>/<role>.md`) **using the `Write` tool** — NEVER `cat <<EOF`, `python3 << 'EOF'`, `cat >`, `tee`, `printf >`, `echo >`, or any Bash heredoc/redirect. Any heredoc or inline script containing `{` and quotes triggers the brace+quote security prompt; the `Write` tool bypasses this. Return only this one-line confirmation: `Written to <path>`. The orchestrator will read the file. The JSON structure below is unchanged.

```json
{
  "verdict": "PASS" | "FAIL",
  "findings": [
    {
      "severity": "critical" | "major" | "minor",
      "fix_type": "mechanical" | "design_decision",
      "file": "path/to/file",
      "line": 42,
      "description": "What's wrong and why it matters",
      "suggestion": "How to fix it"
    }
  ],
  "summary": "One-line summary of the review"
}
```

Rules:
- `FAIL` if any `critical` or `major` finding exists
- `PASS` if only `minor` findings or none
- Keep findings actionable and specific — reference file paths and line numbers from the diff
- Do not fabricate findings; if the diff is clean for your area, return PASS with empty findings
- Batch independent reads (the diff, source files, `CLAUDE.md`) into one message of parallel tool calls; every extra turn re-reads the whole context
- **All test suites have already passed** before this code was committed. Do not speculate about runtime failures or flag "this might break tests." Focus on what the code does, not whether it runs.

Classification rules for `fix_type`:

**`mechanical`** — exactly one correct resolution, no trade-offs (examples, illustrative):
- Import ordering/grouping violations
- Dead code, dead parameters, unused imports
- Naming convention fixes (single-letter vars, `error` → `_`, quoted type hints in a language with lazy annotations)
- Debug artifact removal (console.log, print, `sleep`/hard-coded wait → an explicit readiness wait)
- Vacuous/redundant assertions
- Log level corrections (e.g. error-level → warning-level for routine events)
- Blank line / whitespace style fixes

**`design_decision`** — multiple valid approaches, requires human judgment:
- New class/base class extraction or schema hierarchy changes
- New test file creation or significant test additions
- Public API or function signature changes
- Architectural choices (refactor vs wrapper, merge vs split)
- Any finding where the suggestion contains "could" or "consider" with alternatives

## Subagent 1: Safety & Security

Review the diff for security vulnerabilities and dangerous operations:
- XSS, SQL injection, command injection, path traversal
- Leaked secrets, API keys, tokens, passwords in code or config
- Destructive operations without safeguards (rm -rf, DROP TABLE, etc.)
- OWASP Top 10 vulnerabilities
- Unsafe deserialization, insecure cryptographic usage
- Missing input sanitization at system boundaries

## Subagent 2: Correctness

Review the diff for logic and functional errors:
- Off-by-one errors, wrong comparisons, inverted conditions
- Type mismatches, None/null handling gaps
- Edge cases not handled (empty inputs, boundary values)
- Race conditions or concurrency issues
- Incorrect API usage or wrong function signatures
- Broken control flow (unreachable code, missing returns)

## Subagent 3: Hygiene (Simplicity, Completeness & Consistency)

One agent covers the three checklists below (formerly Subagents 3, 5 and 6). Evaluate each checklist separately and write one complete schema-valid JSON file per checklist: `<tmp-dir>/simplicity.md`, `<tmp-dir>/completeness.md`, `<tmp-dir>/consistency.md` (a clean checklist gets `PASS` with empty findings, never a missing file). Read the diff and the working repo's `CLAUDE.md` once and reuse them for all three. Return only the three `Written to <path>` lines.

### 3a. Simplicity & Conciseness

Review the diff for unnecessary complexity:
- Over-engineering: abstractions for single-use cases
- Dead code, unused imports, unreachable branches
- Verbose patterns that have simpler equivalents
- Premature generalization or excessive configurability
- Code that could be replaced with stdlib/framework utilities
- Unnecessary indirection or wrapper functions

## Subagent 4: Test Coverage

All test suites (integration, UI, unit, JS) have already passed before this code was committed. Do not question whether tests pass at runtime. Instead, review the diff for coverage gaps in the test code itself:
- New functions/endpoints/classes without corresponding tests
- Modified behavior without updated tests
- Missing edge case tests (error paths, boundary values)
- Tests that don't actually assert the new behavior
- Frontend changes without UI test coverage
- Deleted tests without replacement coverage for the same behavior

### 3b. Completeness & Cleanup (part of the Hygiene agent, Subagent 3)

Review the diff for leftover artifacts and incomplete work:
- Debug code: console.log, print(), window.* globals, debugger statements
- Commented-out code blocks
- TODO/FIXME/HACK comments introduced in this diff
- Incomplete implementations (stub functions, placeholder values)
- Missing error messages or user-facing strings
- Temporary files or test fixtures committed accidentally

### 3c. Consistency & Style (part of the Hygiene agent, Subagent 3)

Review the diff for adherence to the **working repo's** conventions (read its `CLAUDE.md` for the authoritative rules; the items below are illustrative of the kinds of rules to check):
- Naming conventions for the repo's languages (e.g. snake_case vs camelCase where each applies)
- No single-letter / non-descriptive variable names, if the repo requires descriptive names
- Type-annotation conventions (e.g. no quoted type hints in files that use lazy/deferred annotations)
- Module-communication conventions (e.g. no global/window state where the repo forbids it)
- Follows existing patterns in the codebase for similar operations
- Import ordering and organization matches surrounding code and the repo's declared import style

## Subagent 5: Integration Risk

Review the diff for cross-module and deployment risks:
- Breaking changes to public APIs, shared interfaces, or database schemas
- Missing database migrations for model changes
- Changes that could break other modules importing from modified files
- Config or environment variable changes without documentation
- Dependency version changes that could cause conflicts
- Changes to shared utilities that affect multiple callers
- **Registry / paired-structure invariants declared in the working repo's `CLAUDE.md`**: when the repo documents that certain structures must stay in lock-step (e.g. an enum member requiring a matching entry in one or more registry/model/description tables, or a generated-artifact/audit step that must be re-run), verify the diff keeps all sides aligned and flag any half-applied change.
