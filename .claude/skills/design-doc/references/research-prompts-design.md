# Design-Doc Research Subagent Prompts

These prompts drive Step 4 of `/design-doc` — grounding a feature's open design questions in
what the codebase already does, before asking the user anything. Focus is **precedent and
constraint discovery**, not implementation depth (that's `/plan-creator`'s
`references/research-prompts.md` once the design doc is settled).

Each subagent is a research-only agent (`model: "sonnet"` explicitly set) exploring via
Glob/Grep/Read. It receives the feature description from Step 3 and the path to write its
findings.

> **Stack note:** the example values in the `findings` shapes below are **illustrative only** —
> they name generic concepts (an analog feature, a shared communication mechanism, a data model,
> an analytics/metrics registry) rather than any one stack's APIs. Substitute whatever the
> working repo actually uses; discover the real names by reading, never assume them.

## Response Format (all subagents)

> Write your complete response to `~/code/plans/<repo>/open/<topic>/tmp/research-<focus>-design.md`
> using the `Write` tool — never a Bash heredoc/redirect. Return only: `Written to <path>`.

```json
{
  "area": "patterns | integration | constraints",
  "files_read": ["..."],
  "findings": { ... },
  "open_questions_narrowed": ["<question the codebase couldn't fully answer, made specific>"],
  "summary": "2-3 sentences"
}
```

---

## Subagent 1: Existing Pattern & Convention Discovery

**Role:** find analogous features already built, so the new feature follows precedent instead
of inventing a fresh pattern.

**What to read:** similar features in the same domain (e.g. an existing panel/modal/filter/list
if this feature is one), `CLAUDE.md` established patterns, naming/file-layout conventions in the
affected area. **If the feature is UI-facing**, also read the app's actual visual conventions —
CSS custom properties/theme variables, the color palette in use, existing component/modal/panel
markup and class names, spacing/typography patterns — so the design doc's Codebase Context can
ground any visual decision in real values instead of the design doc inventing new ones.

**Checklist:** what's the closest existing analog? What pattern/convention does it follow
(component structure, shared communication mechanism, user-facing-string convention, etc.)? Would
deviating from it need explicit justification? For UI-facing features: what colors/spacing/
component patterns does the closest analog already use, and are there any existing "off-brand"
exceptions worth flagging?

**Findings shape** (illustrative — replace with the repo's real values):
```json
{
  "closest_analog": {"feature": "<analogous feature name>", "files": ["<path to its implementation>"], "pattern": "<how it's structured — e.g. togglable filter box driven by the app's shared event mechanism>"},
  "conventions_to_follow": ["<repo convention 1 — e.g. destructured object params>", "<repo convention 2 — e.g. user-facing text routed through the app's string/localization layer>"],
  "ui_conventions": {"colors": ["<theme var / color token in use>"], "components_to_reuse": ["<reusable component/shell and its path>"], "notes": "omit this field entirely for non-UI features"},
  "deviation_flags": []
}
```

---

## Subagent 2: Integration & Dependency Surface

**Role:** map what this feature would touch or need to extend, so open questions about "does X
already exist" get answered by evidence, not guesswork.

**What to read:** models/schemas/endpoints in the feature's domain, the app's
analytics/metrics/event registry if the feature represents a trackable action, and any shared
event/message bus for existing related events.

**Checklist:** what already exists that this feature would extend vs. build from scratch? What's
the closest existing endpoint/model/event, if any? Any analytics/metrics/registry implications?

**Findings shape** (illustrative — replace with the repo's real values):
```json
{
  "existing_to_extend": [{"what": "<existing model/component>", "file": "<its path>", "note": "would need a new field/column for X"}],
  "net_new": ["<capability nothing today serves — e.g. an export endpoint>"],
  "metrics_implications": "<e.g. a new domain/analytics event likely needed if the repo tracks user actions — check CLAUDE.md for any metrics-coverage rule>"
}
```

---

## Subagent 3: Constraint & Precedent Discovery

**Role:** surface existing limitations or past decisions that bound the design space, so open
questions don't re-litigate settled constraints.

**What to read:** `CLAUDE.md`, `ARCHITECTURE.md`, any existing plans in
`~/code/plans/<repo>/` (both `open/` and `completed/`) touching the same area, recent git
history for related reverts/limitations.

**Checklist:** any hard technical constraint (e.g. a capability that isn't measured/supported
anywhere today, a fixed dependency version)? Any prior design decision on record that this
feature must respect or explicitly override?

**Findings shape** (illustrative — replace with the repo's real values):
```json
{
  "hard_constraints": ["<e.g. some dimension is not measured anywhere today (per CLAUDE.md) — a feature depending on it isn't buildable without a separate effort>"],
  "prior_decisions": [{"decision": "<a settled design choice>", "source": "<where it's documented — CLAUDE.md section, a prior plan>", "applies_because": "<why it bounds this feature>"}]
}
```
