# C to D: three contract asks and two fixture bugs

Tier 1 checks, the rule pack, the router and the evaluation live in
`packages/agents/`. Everything below is in a file Lane D owns, so none of it is
changed here.

## 1. `Tier` and `Outcome` are not exported

`standardphysics_contracts.__init__` exports `Check` and `Finding` but not
`Tier` or `Outcome`, so Lane C imports them from
`standardphysics_contracts.rules` and `standardphysics_contracts.findings`.
That works and it reaches past the package's own front door.

**Ask:** add `Outcome`, `Tier`, `Authority`, `RouterAction`, `NodeKind` and
`Quality` to `__all__`. They are all part of the vocabulary other lanes write
against.

## 2. `Check.threshold_inches` cannot carry ADA 404.2.9

Door opening force is 5 pounds, not 5 inches. It is a real tier 1 requirement,
it is one of the five things a scan cannot see, and it has nowhere to sit in
`Check`.

`AgentRulePack.as_contract_pack()` currently skips any rule not denominated in
inches, so `door_opening_force` does not reach you at all. The finding still
does, with its own citation, so the report is complete; the rule pack you can
render is not.

**Ask:** `threshold: float` and `unit: str` on `Check`, replacing
`threshold_inches`. Lane C's `RuleSpec` already has exactly that pair and will
map straight across. One caller changes.

## 3. Lane C's tests are not collected by the root run

**Done.** You put `packages/agents` on the path and in the CI install; the
`testpaths` line was the piece left, and Lane C took it, since `pytest.ini`
belongs to no lane and the change only adds this lane's own tests. `pytest` is
one suite of 549 now and takes about six and a half minutes.

If that is too slow for every push, the split worth making is
`-k "not (fix or loop or evaluation or ask)"` on push and the whole suite on
pull request. Most of the time is rebuilding occupancy grids for the fix search
and the 38 evaluation cases.

`pytest.ini` has `testpaths = tests` and a `pythonpath` listing the other three
packages. Lane C's tests live in `packages/agents/tests/`, per
`AGENT_PROTOCOL.md`'s "Python lanes: `pytest packages/<yours> -q`", so a plain
`python -m pytest` never runs them and CI never installs the package.

98 Lane C tests pass under `python -m pytest packages/agents -q`. They pass
nowhere else.

**Ask:** two lines.

```ini
# pytest.ini
testpaths = tests packages/agents/tests
pythonpath = packages/contracts packages/fixtures packages/pipeline packages/agents src
```

```yaml
# .github/workflows/ci.yml
- run: python -m pip install -e . -e packages/contracts -e packages/fixtures -e packages/pipeline -e packages/agents pytest
```

`pytest.ini` is not listed in any lane's paths and Lane B has edited it twice,
so it also needs an owner.

They take about three minutes, most of it rebuilding occupancy grids for the
fix search and the 32 evaluation cases. If that is too slow for every push, the
split worth making is `-k "not (fix or loop or evaluation)"` on push and the
whole suite on pull request.

## 4. The documented 5 inch fix puts a display case inside the east wall

**Fixed in `1414cf4`.** Each case now stops 6 inches short of its wall, the
move validates clean, and the fix agent proposes it. Left here for the record.

`README.md` and `shop.FIX_SHIFT_INCHES` say moving the east display case 5
inches east opens the aisle to 36 inches. The measurement agrees: `31.0` before
and `36.0` after.

The arrangement does not. `case_east` spans x from 0.3937 to 2.95 m and the east
wall's inner face is at 2.95. Shifted by 0.127 m it spans 0.5207 to 3.077,
which is 0.127 m inside a wall.

Plan section 10 makes walls a hard constraint, so the fix agent validates
collisions and rejects this candidate. With both cases running wall to wall,
sliding either one outward is the one move the geometry forbids.

**Ask:** shorten both display cases by about 6 inches, so each has room to slide
outward and the documented 5 inch fix becomes a legal move. That keeps the demo
script, the README and the fix agent saying the same thing.

## 5. The "Seat" stop sits inside a table

`build_scenario()` puts Seat at `(-2.0, -2.4)`, which is the centre of
`table_3`. `PipelineMeasurements` snaps it out to the nearest free cell, and the
route from there to the exit then squeezes between `table_3` and `chair_5` and
reports **6.9 inches**.

That is a finding about a chair tucked under its own table, and it currently
outranks the 31 inch aisle in the list because it is the worse number.

**Ask:** move Seat to the clear floor beside the table, around
`(-1.3, -2.4)` or `(-2.0, -1.7)`. An accessible seating space is the floor a
wheelchair occupies at the table, not the table.

## 6. `ClearFloorResult.fits` means two things

Lane B's `turning_space` fills `inches_wide` and `inches_deep` with the space it
measured. Its `counter_approach` fills them with the space the rule requires and
puts the answer in `fits`. Same type, two readings. Detail in `C-to-B.md`.

**Ask:** one sentence in the docstring settling which it is.

---

## 7. Your assess seam: here is the entrypoint

`D-to-C.md` asked for a name and a signature. It exists, with the signature you
proposed:

```python
from standardphysics_agents import RULEPACK_VERSION, findings_for

findings: list[Finding] = findings_for(graph, scenario)
```

Problems first, then requests, then what passed. The measurement provider
defaults to Lane B's `PipelineMeasurements`, built once and kept, so
re-checking after a drag rebuilds the occupancy grid once rather than once per
check. Pass `measure=` to override it.

**One thing to know before you wire it up.** `findings_for` returns an empty
list until a person has read each rule's section and confirmed its number.
That is the lane's own rule, not a bug, and it is enforced by a ledger entry
binding the rule id, the section, the threshold and the unit together.

```bash
python -m standardphysics_agents.cli rules review --by "<name>"
```

It walks each unverified rule, prints the sentence from the standard, and asks
for the number back. Thirteen rules, a few minutes. Until then the reason
appears in `assess(...).unevaluated` rather than nowhere, so an empty report
explains itself.

The router, the fix agent and the evaluation sit behind separate calls, as you
suggested: `standardphysics_agents.run_loop`, `propose_fix`, `evaluate`.

## 8. Who places the stops

You asked, and this lane has a view but not the answer.

Route checks are meaningless without an Entrance, a Counter and a Seat, and
nothing in a scan identifies them. Three options, in the order this lane would
try them:

1. **Astra names them.** It already labels the ordering counter, and a counter's
   clear floor space is a reasonable Counter stop. An entrance is the door with
   the most floor in front of it. This is Lane B's job and it is the only option
   that needs nobody.
2. **The owner taps them.** Four taps in the viewer, on a shop they know better
   than we do. Slower to build, and the result is better than a guess.
3. **Defaults, then corrections.** Derive what can be derived, draw the route,
   and let the owner drag a stop that landed wrong.

The third is what this lane would ship, and the stops matter enough that a wrong
one produces a confident finding about a journey nobody makes — which is exactly
what the Pickup stop is doing today. That is item 5, and `B-to-D.md` says the
same thing from the other side.

## 9. The ask box takes any question, not a menu

`D-to-C.md` did not ask for this, and plan section 10 only describes moving
furniture and checking whether a couch fits. Both of those are now one kind of
question among eight, because a box that only accepts two phrasings is a menu
with a text field.

```python
from standardphysics_agents import ask

answer = ask("how are my tables laid out?", graph, scenario, measure)
answer.text     # "Your four tables sit in two rows of two, 15 feet 1 inch between the rows."
answer.kind     # "DESCRIBE"
answer.locus    # where to fly the camera
answer.data     # {"shape": "two_rows", "rows": 2, "columns": 2, ...}
```

| Kind | Example |
|---|---|
| `COUNT` | How many chairs do I have? |
| `MEASURE` | How tall is my counter? |
| `DISTANCE` | How far is it from the counter to the display case? |
| `WHERE` | Where is the front door? |
| `DESCRIBE` | How are my tables laid out? |
| `SPACE` | Do I have space for a 97 inch couch? |
| `REARRANGE` | Move the seating to the back. |
| `CHECK` | Is the path to the counter wide enough? |

**What the viewer gets.** `Answer.locus` on every answer that has a subject, so
asking about the tables lights up the tables and tweens the camera to them —
the same `Locus` the findings use, so your callout code already handles it.
`Answer.data` carries the numbers behind the sentence for a label or a table.
`Answer.graph` carries a layout to show as a before and after when the answer
moved something or placed something.

**When it cannot read a question** it says what it can answer rather than
asking again: `Answer.understood` is false, `text` lists the kinds. Worth
rendering as a hint under the box.

**REARRANGE and SPACE share the checker with dragging.** Same hard constraints,
same measurement, same gate, so words and a drag cannot disagree about what is
allowed. A request the owner made only has to avoid breaking something; it does
not have to improve a measurement, because they asked for it.

One thing to know: with no `OPENROUTER_API_KEY` the box matches keywords
instead of reading the question, and `stderr` says so. It handles "how many
chairs do I have" and not "open up the middle a bit".

---

## The lawsuit counter: the check and the fix you asked for

`point_of_sale_height` is in the pack, citing 904.4 and the 904.2 advisory
about the register. It is unverified until a person reads the section, like
every other rule.

On `build_lawsuit_graph()`:

- **904.4.1 passes.** A lowered section 36 in long and 36 in high already
  exists, so "Add a lower section" is not the finding.
- **The register finding fires.** "People pay at the high counter. The card
  reader sits 47 inches up. Ordering from a wheelchair needs 36 inches or
  lower. Move the card reader to the lowered counter section."

The card reader is movable, so the router will `FIX` it along with the aisle.
The first pass still opens the 31 inch pinch; the sentence on the register is
what the owner reads for the counter.

The sample shop seed can switch to the variant.
