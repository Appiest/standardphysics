# marimo

marimo is a reactive Python notebook. A cell is a function, marimo reads which
names each one uses, and changing a value re-runs only the cells downstream of
it. There is no hidden state and no stale output, so a notebook checked into a
repository runs the same way for the next person who opens it.

`notebooks/scenario_sweep.py` uses that to make the review interactive. Its
first half builds a shop from sliders, and the same evaluator that scores the
labelled dataset reads the room they build. Its second half does the same thing
to a room that came off a phone.

```bash
./start.sh                                     # once, to install
.venv/bin/marimo edit notebooks/scenario_sweep.py
```

`marimo edit` opens it with the code visible. `marimo run` serves the same
notebook as an app with the code hidden, which is the version to put on screen.

## The knobs

| Slider | What it moves |
|---|---|
| Gap between the display cases | Rebuilds both cases so the aisle between them is the width you set, without pushing either one through a wall |
| Counter height | The ordering counter, which 904.4.1 holds to 36 in |
| Front door opening | The clear width of the front doorway |
| Keep the seating by the counter | Whether the tables that crowd the counter are in the room |
| Routine | Ordering a drink from inside the door, or walking in from the street |

The routine matters more than it looks. Ordering a drink starts inside the shop,
so the aisle is the tightest thing on the route. Walking in from the street puts
the doorway on the route, and then widening the aisle does nothing — the chart
shows the route failing across the whole range, because the door is the pinch.

## Nothing here is a second implementation

`packages/agents/standardphysics_agents/evaluation/scenarios.py` turns the
knobs into a `Case` and hands it to `run_case`, the same function the Weave
evaluation and the W&B experiment grid call. The notebook draws findings and
reads scorers; it never measures anything itself and it never decides what
passes. A finding that appears while you drag a slider is the finding the
server would report for that room.

That is also why the headline figure is a measurement error. A knob that sets a
dimension is a claim about what a correct measurement of it would be: set the
aisle to 31 in and a route starting inside the door should come back at 31 in.
`measurement_error_in` reads the two against each other, and on every setting
tested it lands inside a rounding error.

Where a knob stops pinning a dimension it claims nothing, and the notebook
shows less rather than guessing. With the street routine the aisle carries no
expected route measurement, and the cited reference line is not drawn, because
crossing 36 in on an aisle axis would not change what the route reports.

## What the sweep chart says

One row per check, and its bar covers the settings where that check reported a
problem. Rows that change their mind come first; a check that is wrong at every
setting comes next; checks that pass throughout are demoted.

A verdict is only known at the settings the sweep visited, so a change is drawn
at the midpoint between the two readings that bracket it. Fewer readings is a
coarser boundary, not a wrong one. The solid reference line is separate: it
comes from the finding's own `required_inches`, so it sits where the standard
puts it rather than where a reading happened to fall. Watching the sampled
boundary land on that line is the point of the chart.

`Readings across the range` is the cost control. Thirteen readings of the
shipped shop take about four seconds. Moving the swept slider itself does not
pay that again: the sweep replaces that knob at every reading, so its current
value cannot change the answer, and the notebook parks it before asking the
cache. Only the marker moves.

## A room off a phone

The shop is geometry we authored, which tests the arithmetic and leaves the
assumptions alone. The second half of the notebook runs the checks over real
RoomPlan captures instead.

Four are wired up. The two Apple samples ship inside the fixtures package, so
they are there in any install. The two phone scans live under `datasets/phone`
and appear only in a checkout that carries them, which
`standardphysics_agents.evaluation.captures.available()` works out by looking.

| Control | What it does |
|---|---|
| Room | Which capture to read |
| From, To | The two things the trip runs between, named off the scan itself. Two sofas come back as `Sofa 1` and `Sofa 2` |
| Move, Along, Distance | Pushes one movable piece up to 24 in either way and measures again |
| Read the pieces it only glimpsed as measured | Stands in for the rescan the router would ask for |

That last switch is the one worth understanding. RoomPlan attaches a confidence
to every piece it recognises, ingest turns medium and low into
`needs_another_look`, and a check resting on one of those asks for another look
rather than ruling. A real capture therefore comes back mostly undecided: the
living room measures a 59.6 in route and declines to call it a pass, because the
fireplace beside it was only glimpsed. `after_a_rescan` writes confidence and
nothing else, so it can stop an answer from being withheld and cannot invent
one.

Rearranging goes through `fix.moves.apply_moves` and is judged by
`fix.constraints.violations`, the pair the fix agent already uses. A nudge can
only be a move the loop was allowed to propose, and when a slider pushes a sofa
into a wall the notebook says which constraint it broke instead of reporting a
measurement of an impossible room.

## The route that leaves the building

On three of the four captures most of the walked path runs outside the scanned
floor, and the plan draws that stretch as a faint dotted aside rather than as
the trip.

This is real behaviour, not a drawing error. The occupancy grid marks a cell
free wherever nothing occupies it, so a capture whose walls do not close leaves
open ground outside the building, and the widest path search will use it: the
living room has a 3.67 m doorway, and going around the outside is wider than
squeezing past the sofa. The shipped fixture cannot show this, because its four
walls enclose the room. The headline number says so too, and reports the share
of the route that left the floor rather than calling the reading the tightest
point of a walk through the room.

## Nothing on a capture is scored

`scenarios.run_knobs` calls `run_case`, which scores. `captures.review` calls
`assess` and stops there. A knob that sets a dimension is a claim about what a
correct measurement of it would be, and nobody measured these rooms by hand, so
there is no correct answer to read the pipeline against. Reading precision
against a room nobody labelled would score a missing label as a wrong answer.
What a capture shows is the finding and the confidence behind it.

## Rules a person has to read first

A check runs when somebody has read its section and typed the number back.
Nothing is verified in a fresh clone, so the notebook offers to read every rule
as verified and says plainly that it has. Numbers produced that way are for
development. `standardphysics-agents rules review --by "<name>"` is where a
real reader is recorded.
