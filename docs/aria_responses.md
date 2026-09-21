# What ARIA said

ARIA's answers to the six questions in [`aria.md`](aria.md), asked against the
nine runs of the first grid: three cell sizes (15, 25 and 50 mm) crossed with
three ladder depths (4, 8 and 16 candidates), 39 cases each.

Pasted from the chat. The tables are rebuilt as markdown and the chat
scaffolding is cut; every number and every conclusion is ARIA's own. What we
changed because of it is at the end.

---

## Which field moves `finding_precision`, and which only moves cost?

> `cell_size` moves `finding_precision`; `fix_candidates` does not. `cell_size`
> also dominates cost, while `fix_candidates` only produces small, inconsistent
> wall-time variation.

| Grouping | Value | Mean `finding_precision` | Measurements | Mean wall time |
|---|---|---|---|---|
| `cell_size` | 0.015 | 96.53% | 4,878 | 55.97 s |
| | 0.025 | 100.00% | 4,732 | 19.45 s |
| | 0.050 | 96.53% | 4,345 | 4.22 s |
| `fix_candidates` | 4 | 97.69% | 4,652 | 26.40 s |
| | 8 | 97.69% | 4,652 | 26.54 s |
| | 16 | 97.69% | 4,652 | 26.71 s |

> Within every `cell_size`, changing `fix_candidates` from 4→16 leaves both
> precision and measurements exactly unchanged. Wall-time changes range from
> −1.23 to +2.39 seconds, so there is no consistent cost trend — mostly runtime
> noise.
>
> Recommendation: use `cell_size=0.025` for perfect observed precision, or 0.05
> for roughly 4.6× lower wall time at a 3.47-point precision cost. Keep
> `fix_candidates=4`; larger values show no measured benefit. The next useful
> experiment is replicated runs at `cell_size` 0.025 vs 0.05 to verify that
> frontier.

It checked for the confound before reporting the effect: *"the raw costs suggest
one 'effect' may be runtime noise rather than a real candidate-budget trend."*

## Which check and which cases account for the precision difference?

> The entire precision difference comes from one check: `turn_clear_width`,
> which is a false positive in two cases at `cell_size` 0.015 and 0.05.

| Case | Expected problems | Extra finding at 0.015/0.05 | Case precision | At 0.025 |
|---|---|---|---|---|
| `lawsuit_counter` | `point_of_sale_height`, `route_clear_width` | `turn_clear_width` | 0.6667 | 1.0000 |
| `door_clearance_blocked` | `door_maneuvering_clearance` | `turn_clear_width` | 0.5000 | 1.0000 |

> Across the 24 cases with a defined precision: 0.025: all 24 score 1.0 →
> 1.0000. 0.015 and 0.05: 22 score 1.0, plus 0.6667 and 0.5 → 0.9653. So the
> drop is exactly (1−0.6667+1−0.5)/24 = 0.03472.
>
> There is also a fix-text difference in `tables_crowd_the_aisle`, but it does
> not affect `finding_precision`. All candidate-count repeats within each
> `cell_size` have identical cases results, confirming this is solely a
> cell-size effect.

This is the same two cases we had already found by reading the grid on disk, and
ARIA reached them from the case tables without being told where to look. It also
hit a real obstacle on the way: *"the convenience filter treated the decimal
config values as a different type and returned no rows,"* then switched to a
projected run read and downloaded only the three tables it needed.

## What is the cheapest cell size that holds every score at 1.000?

> The cheapest qualifying cell size is 0.025 (25 mm). Across all three 25 mm
> runs, all six score metrics are exactly 1.000: `finding_precision`,
> `finding_recall`, `fix_resolves_finding`, `label_accuracy`, `question_recall`,
> `router_action_match`. Its costs are 4,732 measurements and 19.45 wall-seconds
> on average (range: 18.85–20.37).
>
> The cheaper 50 mm setting uses 4,345 measurements and 4.22 seconds, but fails
> the requirement: `finding_precision=0.9653` and `router_action_match=0.9744`.
> Thus, 25 mm is the lowest-cost setting that preserves perfect scores.

`router_action_match` dropping at 50 mm is something we had not noticed. A
coarser grid does not only add a finding, it changes what the router decides to
do next.

## Does `fix_candidates` change any metric at all?

> `fix_candidates` does not change any outcome or workload metric in these runs.
> Only the two timing metrics vary slightly, and the variation is inconsistent
> with a real candidate-count effect.
>
> 21 of 23 metrics were exactly identical across candidate counts at every cell
> size. Unchanged metrics include quality — `finding_precision`,
> `finding_recall`, `fix_resolves_finding`, `label_accuracy`, `question_recall`,
> `router_action_match`, `measurement_error_in` — and work: candidates measured,
> fixes attempted and found, measurements taken, failures, completion, and all
> eight measurement counters. For example, `cost/candidates_measured` remained
> 12–13 regardless of whether `fix_candidates` was 4, 8, or 16.

| Cell size | 4 candidates | 8 candidates | 16 candidates |
|---|---|---|---|
| 15 mm | 54.46 s | 56.60 s | 56.85 s |
| 25 mm | 20.37 s | 18.85 s | 19.14 s |
| 50 mm | 4.36 s | 4.17 s | 4.14 s |
| Mean | 26.40 s | 26.54 s | 26.71 s |

> After controlling for cell size, the estimated wall-time differences versus 4
> candidates were 8 candidates: +0.14 s, p = 0.878; 16 candidates: +0.31 s,
> p = 0.739. The direction even reverses across cell sizes, so this looks like
> runtime noise — not a systematic effect. With only one run per grid cell,
> timing precision is limited, but the exact invariance of all substantive
> metrics is clear.
>
> Recommendation: use `fix_candidates=4` for now. More importantly, verify that
> this config is actually wired into candidate selection; the current evidence
> makes it look inert, or at least non-binding on this dataset.

## Which measurement is asked for most, and how does that change with `cell_size`?

> `measurements/turning_space` is requested most often by a wide margin. Across
> all 9 finished runs: `turning_space` 29,769 requests; `route_clear_width`
> 6,282 — the runner-up; `turn_detail` 2,094; `route_run_below` 2,058.

| Cell size | `turning_space` requests per run | Change |
|---|---|---|
| 0.050 m | 3,017 | baseline |
| 0.025 m | 3,381 | +12.1% |
| 0.015 m | 3,525 | +16.8% vs 50 mm |

> Finer cells increase `turning_space` work, while most other measurements
> barely move. The 50 mm setting reduces `turning_space` requests by 14.4%
> versus 15 mm. Candidate count (4/8/16) made no difference to these counts, so
> `turning_space` is the clearest measurement to optimize if compute cost is the
> priority.

## What should we run next?

> Testing 35 mm probes the unexplored boundary between them.

```yaml
cases: 39
cell_size: 0.035        # 35 mm
fix_candidates: 4
measurements: pipeline
preview_unverified: true
router: local
rulepack_version: 1.0.0
run_fixes: true
```

> Why: 25 mm achieved 1.000 finding precision, recall, router-action match, and
> label accuracy. Increasing candidates from 4→8→16 provided no quality benefit.
> Meanwhile, 50 mm was ~4.7× faster but reduced precision to 0.965 and action
> match to 0.974.
>
> Expected result: approximately 0.25–0.30 seconds/case and 10–12 seconds total,
> roughly twice as fast as the 25 mm/4-candidate baseline. My primary hypothesis
> is that recall and label accuracy remain 1.000, with precision/action match
> either remaining perfect or revealing the first small regression. Either
> outcome identifies whether you can safely raise cell size beyond 25 mm.
>
> There is currently no Launch queue for this entity, so I can't submit it myself
> yet.

---

## What we did with it

We ran the configuration it proposed, and three more around it. The timing came
out as predicted — 35 mm takes 7.8 seconds where 25 mm takes 16.9 — and
precision and recall did hold at 1.000. Then the same runs overturned the
conclusion ARIA had drawn twice, that the ladder does nothing:

| Cell size | `fix_resolves_finding` at 4 | at 8 | at 16 |
|---|---|---|---|
| 20 mm | 0.4444 | 0.4444 | 0.7778 |
| 25 mm | 1.0000 | 1.0000 | 1.0000 |
| 30 mm | 0.8889 | 0.8889 | 1.0000 |
| 35 mm | 0.8889 | 0.8889 | 0.8889 |
| 40 mm | 0.8889 | 0.8889 | 0.8889 |
| 50 mm | 1.0000 | 1.0000 | 1.0000 |

The three cell sizes in the first grid — 15, 25 and 50 mm — all happen to find
every fix in the first four rungs of the ladder. At 20 mm and 30 mm the ladder
decides whether a third of the fixes are found at all. ARIA's reading of the
runs it had was right, and its own proposed next run is what showed the axis
values were the unlucky part rather than the axis.

Its other recommendation, that `turning_space` is the thing to optimize because
it is 71% of all measurement requests, does not pay. Each call reads a clearance
field the provider already built and indexes one cell, so the count is high and
the cost is not: taking the duplicated field lookup out of it moved a 39-case
run from 16.99 to 16.95 seconds.

What shipped is in the last section of [`aria.md`](aria.md).
