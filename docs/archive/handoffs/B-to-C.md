# B to C: Astra now labels before you measure

`reconstruct` runs on ingest. A long storage box against a wall becomes
"Ordering counter" and immovable. Chairs and tables stay movable. Sizes do
not change. Your `service_counters` role list already matches that label.

`standardphysics-agents screen --samples 256` tries many legal slides on the
typed graph. TypeSafe still picks the action; the screen is the 3D search.

---

# B to C: leg 1 was never a finding, and I argued the wrong way twice

## I was wrong about leg 1

I told you twice that Counter to Pickup's 29.79 in was "a true measurement of a
real 0.75 m band" and that the problem was the scenario. The scenario is still
odd, but the number was wrong and the fault was mine.

The audit worked it out: the counter and `table_1` **do not overlap in x**.
Their footprints come within 29.79 in of each other diagonally, across open
floor, several feet from anywhere the route goes. The route passes through
57 in.

My width refinement took the two nearest distinct objects to the pinch and
measured the shortest distance between their footprints. That is the corridor
width when the route runs between them and a diagonal across an empty room when
it does not, and nothing in the code told the difference.

**Fixed.** The pinch now measures across the corridor: step out at right angles
to the direction of travel and report what each side runs into. Where the pinch
genuinely sits between the pair, the exact footprint gap is still used, so the
aisle stays exactly 31.000000 in and the documented fix exactly 36.000000 with
no quantisation. Where it does not, the width is measured from the pinch
outward.

Fixture legs now read **31.00, 57.12, 31.00, 102.89**.

So `fixture_as_shipped` should lose its counter-leg finding, and any case you
built around leg 1 being a real pinch wants revisiting. Sorry for the two rounds
of confident wrong analysis.

## A-9 is held, and it is a question for you

The audit wants `door_clear_width` to set `needs_measurement=True`, and on the
merits it is right: ADA 2010 404.2.3 measures between the door face and the stop
at 90 degrees open, while RoomPlan reports the leaf in the plane of the wall,
which is the hole rather than the width you pass through.

I made the change, ran your suite, and reverted it. **It fails 15 of your tests
and turns every shop into `RESCAN_AREA`.**

```
assert LocalPolicyRouter().decide(router_state).action == "FIX"
E  AssertionError: assert 'RESCAN_AREA' == 'FIX'
```

Every scan has a door, so with the flag set every scan carries a standing
measurement request, and the router asks for it before it ever proposes a fix.
The demo would never reach a rearrangement.

That is a priority question in your router, not a geometry question in my
measurement, so it is yours to decide. Three ways I can see:

- The router prefers a `FIX` it can actually make over a measurement request
  that blocks nothing else.
- The door request is raised once per scan and parked, rather than re-raised
  each pass.
- `needs_measurement` stays off for doors and the door check states its
  assumption instead.

Tell me which and I will land the flag the same hour. Until then A-9 stays
pinned as an expected failure with this note against it, so nobody reads the pin
as an oversight.

## Still yours whenever

`height_locus(node, height_result)` is in `locus.py`; `checks/vertical.py` can
go. And `blocked_but_movable` still expects `ASK_OWNER`.

---

## What this costs you: 5 tests

`test_every_expected_problem_is_reported` and
`test_the_router_picks_the_right_action_every_time` fail, plus three more in
`test_evaluation.py`. All of them are labelled expectations built on leg 1
reporting a finding, and it no longer does, because it never should have.

I pushed anyway rather than sitting on it, because the alternative is worse than
a red label: the demo shop currently reports **"the path to the pickup counter
is too narrow, 29.79 in"** about a corridor that is 57 inches wide. That is a
false finding shown to a shop owner, and it is in `fixture_as_shipped`, which is
what the demo prints.

My suite is green at 163. Your 331 pass and 5 fail. If you would rather have the
old behaviour back while you relabel, say so and I will revert within the hour —
but I do not think we should demo a finding we know is not there.
