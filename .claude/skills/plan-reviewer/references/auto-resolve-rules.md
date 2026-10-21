# Design-Decision Auto-Resolve Rules

Read by the coordinator (Step 3b) to decide, for each `design_decision` finding, whether the user must be asked. Derived from 1,945 past design decisions and the user's recorded choices (back-tested blind, with a held-out set). The goal is fidelity to what the user would choose, not fewer prompts: when a rule's condition is not clearly present in the DD text, ask.

**Mode:** `live`  (set to `shadow` to keep asking everything while logging what each rule would have picked)

## Tiers

| Tier | Rules | Handling | Measured match with the user's choice |
| --- | --- | --- | --- |
| `auto` | R1 add-test, R8a ordering, R9 anchor, R11 guard, R12 extract, R13 harden | Apply the pick without asking; report in the pass summary so the user can revert | ~98% (clean held-out 98.7%, 77 DDs) |
| `confirm` | R5a local-defect, R6a a11y | One batched question with the pick pre-filled | ~96% (clean held-out 91% / 88%) |
| `ask` | everything else, including spec-gap, precedent, docs-drift, hints and keep-as-is nits (each measured 70-86% on held-out data) | Individual `AskUserQuestion`, recommendation first | n/a |

A DD is `auto`/`confirm` only if exactly one rule fires, no guardrail trips, and the pick is a single option id. Otherwise `ask`. Reviewer conflicts (`classification: conflict`), critical findings and anything touching security boundaries, legal claims, data migrations, destructive operations, public contracts or workflow policy are always `ask`.

## Reading the options

Judge only from the DD text. A guardrail trips only when the DD text itself states the guarded condition; a general worry that it might be true is not a trip. Output the BARE option id (`A`, `1`), never `A (Recommended)`.

**MARKER.** Exactly one option carries `(Recommended)` / `(chosen)` / `(user choice...)` in its id cell, description or trade-off cell (`not recommended` does not count). Select it even if a soft NOT-if would trip. Exceptions: another option's own text says it also covers something the marked one does not ("also covers", "fix both", "additionally") -> ask; a HARD guard applies (legal/privacy claim; needs a new data migration not already in the plan; changes a public contract; the R12 scope-expansion phrase; reviewers disagree about facts).

**LEAVE-CLASS.** Never counts as a competing fix: leave / accept / keep as-is; "keep X and rely on Y"; rely on another layer; trust the sub-plan; document; defer; narrow/reword/drop the claim; manual check; remove the defensive code; push handling onto callers.

**HEDGE.** An option whose core action depends on an unverified fact ("if applicable", "if present") is never selected and does not count as a competitor when a non-hedged option exists.

**PARTIAL.** Never select an option whose own text says it leaves part of the stated problem unfixed ("only fixes", "backstop", "reduces exposure") when another option closes all of it. If every gap-closing option is partial, ask.

**LADDER.** Applies only when an option's own text literally says it includes the other ("A plus...", "also", "both", "all N") or the smaller option says "only X". Select the largest, even over a Recommended smaller rung. Exception: if the extra part changes behaviour for existing callers, refactors production code beyond the fix or test, or goes beyond sibling practice, select the largest option without that extra part. Effort-only costs ("more test code", "re-gate", "touches tested code") are not an exception.

**COMPETING-ALTERNATIVES.** A gap-closing option changes code, config or plan structure. When two or more each close the gap (ladders go to LADDER), fire only if: (i) MARKER selects; or (ii) every competitor you are not selecting states a CONSEQUENCE downside in its own text (changes shipped behaviour or an invariant, rejects a legitimate case, is partial, requires restructuring, drags scope in, sets a new shared convention, more state to track, "weaker", "rejected", leaves a stale window) AND the selected option's own downside, if any, is effort-only (extra request, more code or tests, larger diff, re-runs the gate); or (iii) exactly one competitor fails closed / denies the unsafe case and another allows it -> select the fail-closed one. Reviewer order, "minimal/cheapest/simplest", or a lone precedent citation are never enough.

**TEST-FORM.** When every option is a way of writing or placing a test: fire only if exactly one option says it mirrors / follows the pattern of an existing test in the same file or suite and no other option cites one.

## `auto` tier rules

**R1 add-test.** Signal: a missing test for new or changed branching, guard, safety, fail-closed or error-path logic, or a stated contract / Must-be-true, in code or plan this branch adds or touches (including restoring coverage the branch removed). Select, in order: (a) MARKER; (b) exactly one non-HEDGE option adds the test and the rest are LEAVE-CLASS; (c) LADDER; (d) other options are non-test fixes or spec choices -> COMPETING-ALTERNATIVES; (e) every option adds a test and they differ only in form (layer, new vs fold into existing, file): fire only via MARKER, a consequence downside on every other form option ("weaker", "accept no unit coverage", "needs approval"), or TEST-FORM; otherwise ask. Not if the DD text says: the test needs a framework or tool not in the repo yet (a new case in an existing unit/integration/e2e/Playwright suite is not new infrastructure); the test is brittle, flaky or tautological; sibling code deliberately skips it. The leave option's own argument ("covered transitively") is not a guard.

**R8a ordering (execution-blocking).** Signal, any: a step's own Red/Green/verify gate cannot pass; a step's code, config, tests, hints or docs reference a file, route, target, helper, key or marker that only a later step creates (or no step creates); an earlier step's change makes the plan's own later edits or gates get refused or prompt. Select the option that moves work between existing steps (pull the artifact or a stub forward, or move the referencing item later), only when every other option keeps the order (note, accept, workaround, drop) or merges / swaps / renumbers steps. If two or more options move work, fire only if one is marked. Not if: the only moving option merges, swaps or renumbers steps; a keep-order option is marked Recommended; the problem is only an intermediate behavioural gap between commits with no failing gate and no missing reference (ask, workflow-policy).

**R9 anchor.** Disagreement about line numbers, names or code anchors: select the symbol-anchored option; if both are symbol-anchored, MARKER decides, else ask.

**R11 guard.** Signal: a race, hang, torn-state or latent-misuse risk in tooling, infra, concurrency or data-safety code. One option leaves behaviour unchanged (accepts / documents / pushes handling to callers); another adds a guard (lock, atomic write, timeout, bounded wait, trap ordering, catch-and-requery, or for a latent risk a regression test that fails when the hazard becomes reachable). Select the guard; with two different guards apply COMPETING-ALTERNATIVES. Not if: the accept option is marked Recommended; the context says the risk is already documented as a deliberate caveat or best-effort behaviour; the guard is partial (its own text says best-effort or cannot cover some path); the context itself (not the accept option's own argument) says the impact is harmless or self-recovering. "Benign only because not yet reachable" does not count as harmless.

**R12 extract.** Signal, any: the same logic duplicated near-verbatim in 3+ places; 2+ production copies where one re-implements an existing named function; any security- or authorization-relevant duplicate. The extract option must name the shared helper (may be new). Not if: exactly 2 test-only copies; the text says the copies differ in shape; the DD says extracting would edit files this branch does not otherwise change (ask, scope-expansion). "Pre-existing", "touches N files" and "churn on tested code" do not count.

**R13 harden.** Signal: a fail-closed or defence-in-depth guard (about 10 lines or fewer) that closes a stated gap. Select harden when MARKER; or all other options are LEAVE-CLASS; or LADDER; or COMPETING-ALTERNATIVES incl. fail-closed. Not if the DD text says: the guard rejects a currently legitimate caller, input or flow; or the diverging sibling behaviour is a deliberate design choice (a sibling with the same pre-existing gap, "matches the sibling", "no privilege boundary" do not count).

## `confirm` tier rules

**R5a local-defect.** Signal: a concrete failure scenario is stated and an option is a local fix whose text states no public-interface change. Fire when: MARKER; or all other options are LEAVE-CLASS (or delete-the-vacuous-check); or LADDER; or COMPETING-ALTERNATIVES (ii)/(iii); or the DD is about how to write a test and TEST-FORM holds. Not if the DD text says: the plan or a prior DD explicitly accepted this failure as a known limitation or best-effort (a code comment saying "intentional", or "best-effort" inside the fix option, does not count); the fix needs a new data migration not already in the plan; the selected option changes shipped behaviour for current callers; reviewers disagree about the facts. Two or more fixes with no marker and an alternative lacking a consequence downside -> ask.

**R6a a11y.** Signal: a focus / aria / live-region / accessible-name / touch-target / keyboard / WCAG-timing gap. Fire when: MARKER; or all other options are LEAVE-CLASS (including "keep as a deliberate exception"); or LADDER; or COMPETING-ALTERNATIVES. Not if the selected option's own text says it changes visible layout, content or flow for all users beyond the a11y fix. HEDGE options are never selected. Two or more gap-closing options with no marker and no stated downside -> ask, visible-behavior.

## Always ask (measured too unreliable to automate)

Spec-gap (R2, 94% overall but 86% clean), precedent (R3, 70% clean), docs-drift (R4, 83%), hints (R7, 80% clean), keep-minor-nit (R10, 83%), plus: reviewer conflicts, scope expansion, anything with two or more fixes that each close the gap without a MARKER or a stated consequence downside, and every DD no rule fires on.

## Calibration notes

- First-listed option matched the user's choice 85% of the time overall, but position is not a recommendation (129 of 420 push-review tables listed "keep / accept" first); only an explicit `(Recommended)` marker counts. The user followed a marked first option 91% of the time.
- The remaining 1-2% of `auto` misses were mostly "which form of the fix" calls (which test layer), where nothing in the text predicts the choice.
- Re-measure: every review file records `*(auto-resolved: Rn)*`, `*(accepted pre-filled: Rn)*` or `*(shadow: Rn would pick X)*` beside `**Chosen:**`. A user revert of an auto-resolved DD, or an override in the confirm batch, is a false positive: tighten or demote that rule. Promote or demote a rule only on 20+ new decisions.
