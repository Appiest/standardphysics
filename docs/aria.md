# ARIA

ARIA is CoreWeave's research agent inside Weights & Biases. It reads the runs in
a project — the config each run was launched with, the metrics it logged, the
tables behind it — and answers questions about them in panels and reports. It
will also design the next experiment and say what it expects to see.

So the work on our side is to put the evaluation in the project in the form
ARIA reads: one run per configuration, flat hyperparameters in the config,
every score and every cost as a metric, and the per-case results in a table
behind each run.

`python -m standardphysics_agents.cli experiments` does that.

## What a run holds

One run is one configuration of the shop review, scored against all 39 labelled
cases from `packages/agents/standardphysics_agents/evaluation/dataset.py`.

**Config.** Every knob that changes what the system does without changing what
a correct answer is.

| Field | What it sets |
|---|---|
| `cell_size` | The occupancy grid every width and clearance is measured on, in metres. The pipeline ships 0.025. |
| `fix_candidates` | How many arrangements the fix agent measures before it gives up. |
| `measurements` | `pipeline` for Lane B's real geometry, `stub` for the fixtures' simplified stand-in. |
| `router` | `typesafe` for the router, `local` for the labelled policy behind it. |
| `run_fixes` | Whether the fix agent runs at all. |
| `preview_unverified` | Whether the run treated the rule pack as verified. A run with this on is not evidence about a shop. |
| `rulepack_version`, `cases` | Which rules and how many cases produced the numbers. |

**Metrics.** The seven scorers keep the names `scorers.py` gives them, so a
metric means the same thing here and in Weave's Evals tab. `measurement_error_in`
is an error and lower is better; the other six are shares of the cases.

Cost sits under `cost/`. `cost/measurements_taken` is the one to compare across
configurations: it counts every measurement the run asked for, which is the same
number every time, where `cost/wall_seconds` moves with the machine. Each
measurement in the provider gets its own tally under `measurements/`, so a run
says which question it spent its time on.

**The case table.** One row per case: what the checks reported, what the case
expected, which action the router chose, whether the fix held, and every score.
This is what ARIA reads to get from a mean back to the cases behind it.

## Running the grid

```bash
python -m standardphysics_agents.cli experiments              # the default grid
python -m standardphysics_agents.cli experiments --dry-run    # name the configurations
python -m standardphysics_agents.cli experiments --cases 5    # a quick look
```

The default grid crosses three cell sizes with three ladder depths, which is
nine runs and about three minutes. The grid saves to `runs/experiments.json`
first and uploads second, so a missing key costs the upload rather than the
numbers.

`WANDB_PROJECT` decides where the runs land, and it is the same project Weave
traces into, so the traces and the experiments sit together. Signing in works
either way: `WANDB_API_KEY` in `.env`, or `wandb login` once on the machine.

`WANDB_ENTITY` is the team that owns the project. Leave it empty and wandb uses
the default entity of whoever signed in, which is their personal one — the runs
land, and ARIA will not answer about them. The command prints the entity and
project it used, so check that line says the team.

Nothing is uploaded until one of those is set. Until then the command says so
and writes the grid to disk, which is how the numbers below were produced.

Every check is off until a person has read its section and confirmed its number,
and a grid with no checks enabled scores nothing. `--preview-unverified` scores
as if every rule had been read, which is how the numbers below were produced,
and it records that in the config.

## What a person has to set up

| What | Why an agent can't |
|---|---|
| The W&B project is a team project, not a personal one | ARIA only works in team projects |
| An org admin turns on **Smart features** under Settings, Privacy | Account access |
| `wandb login`, or `WANDB_API_KEY` in `.env` on the demo machine | Account access |
| `WANDB_PROJECT` and `WANDB_ENTITY` pointing at that team project | Account access |

ARIA needs W&B Multi-tenant Cloud. Open it with **Ask ARIA** at the top right of
the project.

## Questions to ask it

These are written against the metrics above, because a question naming a real
field gets an answer with a panel behind it.

1. Group the runs tagged `shop-review` by `cell_size` and `fix_candidates`. Which
   field moves `finding_precision`, and which one only moves cost?
2. `finding_precision` is 1.0000 at `cell_size` 0.025 and 0.9653 at both 0.015
   and 0.05. Open the `cases` table on those runs and tell me which check and
   which cases account for the difference.
3. Plot `cost/measurements_taken` and `cost/wall_seconds` against `cell_size`.
   What is the cheapest cell size that holds every score at 1.000?
4. Does `fix_candidates` change any metric at all? Show the evidence either way.
5. Which measurement under `measurements/` is asked for most, and how does that
   change with `cell_size`?
6. Propose the next configuration to run and say what you expect it to show.

A W&B automation can send one of these on its own: point it at a run finishing
in this project and ARIA starts the conversation. That needs the same account
access as the rest.

## What the grid says

Eleven runs, 39 cases each, rules scored as verified, on an M2 Pro. Every score
not shown is 1.000.

| Cell size | Ladder | Weakest score | Measurements | Seconds |
|---|---|---|---|---|
| 20 mm | 4 | 0.4444 `fix_resolves_finding` | 5,233 | 30.0 |
| 20 mm | 16 | 0.7778 `fix_resolves_finding` | 6,234 | 35.1 |
| 25 mm | 4 | 1.0000 | 4,732 | 16.9 |
| 25 mm | 16 | 1.0000 | 4,732 | 16.9 |
| 30 mm | 4 | 0.8889 `fix_resolves_finding` | 4,842 | 12.3 |
| 30 mm | 16 | 0.9861 `finding_precision` | 5,203 | 13.4 |
| 35 mm | 4 | 0.8889 `fix_resolves_finding` | 4,803 | 7.8 |
| 35 mm | 16 | 0.8889 `fix_resolves_finding` | 5,556 | 9.6 |
| 40 mm | 4 | 0.8889 `fix_resolves_finding` | 4,261 | 5.8 |
| 40 mm | 16 | 0.8889 `fix_resolves_finding` | 4,261 | 5.8 |
| stub measurements | 4 | 0.3796 `finding_precision` | 1,422 | 0.1 |

The shipped 25 mm is the only cell size that holds every score, and it is not a
plateau: 20 mm and 30 mm both lose findings, 35 mm holds precision and recall
but not the fixes, 40 mm holds precision and loses recall. Scores flicker with
the resolution rather than falling off past a point.

Behind the precision column is one check on two cases. `lawsuit_counter` and
`door_clearance_blocked` report a `turn_clear_width` problem the labels do not
expect, at every cell size except 25 mm. The measurement behind it:

| Cell size | `lawsuit_counter` | `door_clearance_blocked` |
|---|---|---|
| 15 mm | 30.7 in | 40.2 in |
| 20 mm | 26.8 in | 40.9 in |
| 25 mm | nothing reported | nothing reported |
| 30 mm | 30.7 in | nothing reported |
| 50 mm | 33.6 in | 43.3 in |

403.5.2 asks for 48 inches at the turn, so every number there is a shortfall.
The widths themselves are steady to a thousandth of an inch across that range,
so what moves is whether a turn is detected at all: the turn is recognised from
the path the widest-path search returns, and that path is laid out on the grid.
Lane C's `needs_human` already carries the approximation this rests on —
geometric turn detection at about 122 degrees.

Two things follow, and neither is an agent's to decide. Whether those two shops
contain a 180 degree turn is a question for Lane B and the person holding the
rule pack. Whether the labels or the check are right decides which one changes.
It is written up in [`handoffs/C-to-B.md`](handoffs/C-to-B.md).

## The improvement we shipped

ARIA read the first grid — three cell sizes crossed with three ladder depths —
and concluded twice that `fix_candidates` does nothing: 21 of 23 metrics
identical across 4, 8 and 16 candidates at every cell size, and the two that
moved were wall clocks at p = 0.88 and p = 0.74. It recommended keeping the
ladder at 4, and added one thing to check: *"verify that this config is actually
wired into candidate selection; the current evidence makes it look inert, or at
least non-binding on this dataset."*

It is wired, and a test pins that. What made it look inert is the grid it was
given. The three cell sizes in it — 15, 25 and 50 mm — all happen to find every
fix in the first four rungs. ARIA's own proposed next run is what exposed this:
it asked for 35 mm because the boundary between 25 and 50 was unexplored, and
running that plus 20, 30 and 40 mm showed the ladder deciding whether a third of
the fixes are found at all.

| Cell size | `fix_resolves_finding` at 4 | at 8 | at 16 |
|---|---|---|---|
| 20 mm | 0.4444 | 0.4444 | 0.7778 |
| 30 mm | 0.8889 | 0.8889 | 1.0000 |

So two changes, both in `packages/agents`:

**The evaluation's ladder went from 8 to 16.** Eight was a guess written into
`FIX_CANDIDATE_LIMIT` with a comment saying it was enough. On the cell sizes
nobody had run it was not: sixteen is where `fix_resolves_finding` stops moving,
and twenty-four measures the same candidates as sixteen everywhere. At the 25 mm
the pipeline ships this costs nothing — the same 13 candidates either way.

**The grid's axis values changed rather than the axis.** Cell size now steps
5 mm either side of 25, and the ladder is 4 against 16, the two values whose
outcomes differ. Every row in the table above differs from its neighbour, where
six of the old nine runs were duplicates of the other three.

Two of ARIA's findings we tested and did not ship. Its cheapest-cell-size answer
of 25 mm stands, but as a coincidence rather than a frontier, so nothing about
the pipeline's default changed on the strength of it. And `turning_space` being
71% of all measurement requests does not make it the thing to optimize: each
call reads a clearance field the provider already built and indexes one cell, so
taking the duplicated field lookup out of it moved a 39-case run from 16.99 to
16.95 seconds.

Everything ARIA said is in [`aria_responses.md`](aria_responses.md).
