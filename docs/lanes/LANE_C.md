# Lane C — Checks, router, evaluation

**You decide what counts as a problem and what the loop does next.** Rules with real citations, checks that run on real geometry, TypeSafe choosing the next action, and Weave evaluations that gate whether a fix is accepted.

Read `docs/PLAN.md` section 8. Read `docs/AGENT_PROTOCOL.md` before your first commit.

## You own

```
packages/agents/**
PROGRESS_C.json
docs/handoffs/C-to-*.md
```

## You can rely on today

- `packages/contracts/` — `RulePack`, `Finding`, `Locus`, `Proposal`, `Assessment`, and the `MeasurementProvider` protocol
- `packages/fixtures/standardphysics_fixtures/stub_measurements.py` — a working `MeasurementProvider` returning known values from the fixture shop, including the 31-inch pinch. Build every check against this. Swap to Lane B's real implementation when their `PROGRESS_B.json` lists it in `ready_for_others`; nothing in your code changes but the constructor argument.
- `packages/fixtures/standardphysics_fixtures/data/shop.scene_graph.json` — the same synthetic shop

## Human tasks — flag these to your person

| What | Why an agent can't | When |
|---|---|---|
| TypeSafe event credentials and the real quickstart | Account access, possibly a conversation at a sponsor table | **First hour** |
| OpenRouter key with zero data retention and a credit limit | Account access and a billing decision | **First hour** |
| W&B project created, API key in the environment | Account access | **First hour** |
| Verify every threshold against primary ADA source text | A wrong number invalidates the whole demo and a judge will ask | Before each check ships |
| A second person re-checks each citation | Same reason | Before feature freeze |
| Schedule the accessibility professional | Human scheduling | **First two hours** |

Threshold verification is the one thing in this lane an agent must not do alone. Agents draft the rule pack; a human reads the section and confirms the number before the check is enabled.

## Build order

**1. Rule pack structure.** Versioned, with authority, edition, section, threshold in original legal units, and applicability. Conversion happens once, at the display boundary — a rounded label never changes an acceptance threshold.

**2. Tier 1 checks.** Against `MeasurementProvider`, using the stub.

| Check | Threshold | Source |
|---|---|---|
| Route clear width | 36 in min; 32 in permitted for at most 24 in, between segments at least 48 in long and 36 in wide | ADA 2010 403.5.1 |
| Clear width at a 180 degree turn | Around an element under 48 in wide: 42 in approaching, 48 in at turn, 42 in leaving; not required at 60 in or more | ADA 2010 403.5.2 |
| Passing space | On routes under 60 in wide: 60 by 60 in, or the specified T | ADA 2010 403.5.3 |
| Turning space | Where required: 60 in circle or the specified T | ADA 2010 304.3 |
| Door clear width | 32 in min between face and stop at 90 degrees | ADA 2010 404.2.3 |
| Service counter | Parallel approach: 36 in accessible length, 36 in max height, adjacent clear floor space | ADA 2010 904.4.1 |
| Exit path | Path from each occupied area to an exit, unobstructed | CBC Chapter 10, to review |

*Done when the fixture's 31-inch aisle produces exactly one route-width finding with the right locus and citation.*

**3. Finding copy.** Every finding carries a title a shop owner understands, then the measurement, then the fix.

> **The path to the counter is too narrow**
> It's 31 inches at the tightest point. Wheelchairs need 36 inches.
> Move the two tables by the window 5 inches apart.

Follow section 2 of the plan. No jargon, inches not meters, and never a sentence describing what we did not check.

**4. Things a scan cannot see.** Threshold height at the entrance (303), door hardware (404.2.7), door opening force (404.2.9), floor surface and mats (302), restroom (603, 604). Each becomes one specific question with a photo request, phrased as an action that gets something.

**5. TypeSafe router.** A closed set of actions whose structured output drives real control flow: `FIX`, `RESCAN_AREA`, `ASK_OWNER`, `ESCALATE`, `DONE`. Test malformed, contradictory and truncated output — an invalid action fails closed and authorizes nothing. *Done when a real API response changes which branch runs, and a corrupted one changes nothing.*

If TypeSafe is not available by 3:00 PM Saturday, run a labeled local policy behind the same interface and tell your person to drop the track from the pitch.

The fix agent's model calls go through OpenRouter with the OpenAI SDK, the
same way Lane B calls Astra: `base_url="https://openrouter.ai/api/v1"`,
`OPENROUTER_API_KEY`, model from `OPENROUTER_MODEL`. `tracing.init` turns
Weave's automatic integrations off and the loop opens its model spans by hand,
so leave OpenRouter's Broadcast to Weave setting off too, or every call is
traced twice.

**6. Fix agent.** Proposes translations and rotations of movable nodes only. No resize, no fixture movement, no leaving the floor, no shrinking an obstacle. Keeping the owner's furniture is a hard default — placement is adjustable, inventory is not. After three failed attempts say "We couldn't find an arrangement that works" and offer one specific relaxation for the owner to approve.

**7. Weave tracing.** `weave.init()` at startup, `@weave.op` on every agent call and check. The whole loop should read as one trace tree.

**7b. The experiment grid.** `cli experiments` scores the same cases against nine configurations and logs each one as a W&B run, which is the form ARIA reads. Cost travels with the scores: every measurement asked for, counted. See [`docs/aria.md`](../aria.md).

**8. Evaluation.** About 25 labeled cases: fixture variants plus real scans as they arrive. Cover clean passes, real violations, ambiguous objects, thin coverage, and cases where the right answer is to ask rather than guess. Scorers: `finding_precision`, `finding_recall`, `measurement_error_in`, `label_accuracy`, `router_action_match`, `fix_resolves_finding`.

**9. The gate.** A candidate layout is accepted only when its evaluation completes and strictly improves, with no new failures and no lost coverage. This is the difference between using Weave and using Weave well, and it is what the prize is judged on.

## Rules you enforce

No agent changes a threshold or drops a check to improve a score. No agent writes a dimension. A check resting on geometry marked `needs_another_look` becomes a request, not a red finding.

## Done means

Every threshold traces to a cited section a human verified, the fixture's known pinch produces exactly the right finding, real TypeSafe output changes behavior while malformed output authorizes nothing, and the evaluation gates acceptance with retrievable per-case results.
