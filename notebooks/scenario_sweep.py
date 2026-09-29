import marimo

__generated_with = "0.24.2"
app = marimo.App(width="medium", app_title="One shop, one knob at a time")


@app.cell
def _():
    from dataclasses import replace

    import marimo as mo

    return mo, replace


@app.cell
def _():
    from standardphysics_agents.evaluation import scenarios
    from standardphysics_agents.evaluation.configuration import setup
    from standardphysics_agents.evaluation.runner import action_name
    from standardphysics_agents.rules import load_ledger, load_pack

    return action_name, load_ledger, load_pack, scenarios, setup



@app.cell
def _():
    import scenario_sweep_panels as panels
    from scenario_sweep_charts import (
        KNOBS,
        bounds,
        chart,
        knob_label,
        legend,
        read_sweep,
        rule_title,
    )

    return KNOBS, bounds, chart, knob_label, legend, panels, read_sweep, rule_title


@app.cell
def _(mo):
    mo.md("""
    # One shop, one knob at a time

    Move a slider and the shop changes shape. The same checks the server
    runs read the new room, so a measurement that appears here is the
    measurement an owner would be shown.
    """)
    return


@app.cell
def _(KNOBS, mo, scenarios):
    routine = mo.ui.dropdown(
        options={each.name: each.id for each in scenarios.ROUTINES.values()},
        value="Order a drink",
        label="Routine",
    )
    aisle = mo.ui.slider(
        *KNOBS["aisle_inches"][1:],
        value=31.0,
        label="Gap between the display cases",
        show_value=True,
    )
    counter = mo.ui.slider(
        *KNOBS["counter_inches"][1:],
        value=47.0,
        label="Counter height",
        show_value=True,
    )
    door = mo.ui.slider(
        *KNOBS["door_inches"][1:],
        value=35.5,
        label="Front door opening",
        show_value=True,
    )
    seating = mo.ui.switch(value=True, label="Keep the seating by the counter")

    mo.vstack([routine, aisle, counter, door, seating], gap=0.75)
    return aisle, counter, door, routine, seating


@app.cell
def _(aisle, counter, door, routine, scenarios, seating):
    knobs = scenarios.Knobs(
        routine=routine.value,
        aisle_inches=aisle.value,
        counter_inches=counter.value,
        door_inches=door.value,
        counter_side_seating=seating.value,
    )
    return (knobs,)


@app.cell
def _(load_ledger, load_pack, mo):
    nothing_verified = not load_pack().enabled(load_ledger(), max_tier=1)
    preview = mo.ui.switch(
        value=True,
        label="Read every rule as verified, so every check runs",
    )
    return nothing_verified, preview


@app.cell
def _(preview, setup):
    # The knobs are about measuring a room, so the fix search stays off.
    configuration = setup(preview_unverified=preview.value, run_fixes=False)
    return (configuration,)


@app.cell
def _(bounds, mo, replace, scenarios):
    def parked(knobs, knob):
        """The knobs with the swept one moved to the start of its range.

        The sweep replaces that knob at every reading, so its current value
        cannot change the answer. Parking it keeps the cache key still while
        the slider for it moves, which is what lets the marker follow the
        slider without measuring the room thirteen more times.
        """
        return replace(knobs, **{knob: bounds(knob)[0]})

    @mo.cache
    def reviewed(knobs, configuration):
        """One room through the evaluator.

        Cached, because a slider moves a lot and the geometry behind a given
        setting never changes.
        """
        return scenarios.run_knobs(knobs, configuration)

    @mo.cache
    def swept(knobs, knob, steps, configuration):
        lowest, highest = bounds(knob)
        widths = [
            lowest + (highest - lowest) * step / (steps - 1) for step in range(steps)
        ]
        return scenarios.sweep_knob(knobs, knob, widths, configuration)

    return parked, reviewed, swept


@app.cell
def _(configuration, knobs, reviewed):
    outcome = reviewed(knobs, configuration)
    return (outcome,)


@app.cell
def _(mo):
    mo.md("""
    ## What the checks found
    """)
    return


@app.cell
def _(mo, outcome, panels):
    mo.Html(panels.figure_block(outcome))
    return


@app.cell
def _(mo, outcome, panels):
    findings_list = panels.findings_list
    mo.Html(findings_list(outcome))
    return (findings_list,)


@app.cell
def _(mo):
    mo.md("""
    ## Where it changes
    """)
    return


@app.cell
def _(KNOBS, knob_label, mo):
    swept_knob = mo.ui.dropdown(
        options={knob_label(knob): knob for knob in KNOBS},
        value="Gap between the display cases",
        label="Sweep",
    )
    steps = mo.ui.slider(
        5, 25, 1, value=13, label="Readings across the range", show_value=True
    )
    mo.hstack([swept_knob, steps], gap=2, justify="start")
    return steps, swept_knob


@app.cell
def _(configuration, knobs, parked, steps, swept, swept_knob):
    sweep = swept(
        parked(knobs, swept_knob.value),
        swept_knob.value,
        steps.value,
        configuration,
    )
    return (sweep,)


@app.cell
def _(
    bounds,
    chart,
    knob_label,
    knobs,
    legend,
    mo,
    read_sweep,
    scenarios,
    sweep,
    swept_knob,
):
    def cited_inches(knob):
        """The number to draw the reference line at, when there is one.

        The line lands where the standard puts it rather than where a reading
        fell. It is only drawn when this knob is the thing its check measures:
        walking in from the street makes the doorway the pinch, so crossing
        403.5.1's 36 in on an aisle axis would change nothing and the line
        would be pointing at the wrong dimension.

        A knob can also answer no check at all. The doorway slider sets the
        opening in the wall, and 404.2.3 is about the clear width with the
        door open, which is always smaller, so `PINS` leaves it out and there
        is no cited number to draw.
        """
        pinned = scenarios.PINS.get(knob)
        if pinned is None or pinned not in scenarios.expected_inches(knobs):
            return None
        for _, outcome in sweep:
            for finding in outcome.result.findings:
                if finding.check_id == pinned and finding.required_inches:
                    return finding.required_inches
        return None

    def sweep_view():
        knob = swept_knob.value
        rows = read_sweep(
            [(getattr(moved, knob), outcome.result.verdicts) for moved, outcome in sweep]
        )
        name = knob_label(knob).lower()
        return mo.vstack(
            [
                mo.md(
                    "Each row is a check. Its bar covers the settings it "
                    "reported a problem at."
                ),
                mo.Html(legend(rows)),
                mo.Html(
                    chart(
                        rows,
                        bounds(knob),
                        getattr(knobs, knob),
                        cited_inches(knob),
                        f"{knob_label(knob)} (inches)",
                        f"What each check said as the {name} changes.",
                    )
                ),
            ],
            gap=0.5,
        )

    sweep_view()
    return


@app.cell
def _(mo):
    mo.md("""
    ## What we could not answer
    """)
    return


@app.cell
def _(mo, nothing_verified, outcome, panels, preview):
    reviewer_note = (
        mo.md(
            "Nobody has read a section on this machine yet, so these numbers "
            "come from thresholds no reviewer has confirmed. "
            '`standardphysics-agents rules review --by "<name>"` is where '
            "that happens."
        )
        if nothing_verified and preview.value
        else mo.md("")
    )

    mo.vstack([mo.Html(panels.open_items(outcome)), preview, reviewer_note], gap=0.75)
    return


@app.cell
def _():
    from standardphysics_agents.evaluation import captures
    from standardphysics_contracts import to_inches, to_meters

    return captures, to_inches, to_meters


@app.cell
def _(mo):
    mo.md("""
    ## A room off a phone

    The shop above is geometry we authored, and authored geometry tests the
    arithmetic without testing the assumptions. These rooms came off a phone.
    The walls are where RoomPlan put them, the furniture is whatever it
    recognised, and each piece carries a capture confidence: a check resting
    on something the scan only glimpsed asks for another look instead of
    ruling.

    Nothing here is scored. Nobody measured these rooms by hand, so there is
    no correct answer to read the pipeline against. What you can see is what
    the checks say about geometry that was measured rather than authored, and
    how much of it the scan's own confidence holds back.
    """)
    return


@app.cell
def _(captures, mo):
    scans = captures.available()
    scan = mo.ui.dropdown(
        options={each.name: each.id for each in scans},
        value=scans[0].name,
        label="Room",
    )
    scan
    return scan, scans


@app.cell
def _(captures, mo, scan):
    scanned = captures.load(scan.value)
    suggested = captures.default_aim(scan.value)
    places = list(captures.anchors(scanned))
    pieces = list(captures.movable(scanned))

    origin = mo.ui.dropdown(options=places, value=suggested.start, label="From")
    destination = mo.ui.dropdown(options=places, value=suggested.end, label="To")
    piece = mo.ui.dropdown(options=pieces, value=pieces[0], label="Move")
    direction = mo.ui.dropdown(
        options={name: axis for axis, name in captures.SHIFT_AXES.items()},
        value="north and south",
        label="Along",
    )
    distance = mo.ui.slider(
        -24.0, 24.0, 1.0, value=0.0, label="Distance (inches)", show_value=True
    )
    rescan = mo.ui.switch(
        value=False, label="Read the pieces it only glimpsed as measured"
    )

    mo.vstack(
        [
            mo.hstack([origin, destination], gap=1.5, justify="start"),
            mo.hstack([piece, direction, distance], gap=1.5, justify="start"),
            rescan,
        ],
        gap=0.75,
    )
    return destination, direction, distance, origin, piece, rescan


@app.cell
def _(
    captures,
    destination,
    direction,
    distance,
    origin,
    piece,
    rescan,
    scan,
    to_meters,
):
    aim = captures.Aim(
        capture=scan.value,
        start=origin.value,
        end=destination.value,
        moved=piece.value,
        after_rescan=rescan.value,
        **{direction.value: to_meters(distance.value)},
    )
    return (aim,)


@app.cell
def _(captures, mo, replace, to_meters):
    NUDGE_INCHES = (-24.0, 24.0)
    """How far a piece may be pushed either way."""

    def parked_nudge(aim, axis):
        """The aim with the nudged axis at the start of its range.

        Same reason as `parked`: the sweep sets that axis at every reading, so
        holding it still keeps the cache key still while its slider moves.
        """
        return replace(aim, **{axis: to_meters(NUDGE_INCHES[0])})

    @mo.cache
    def surveyed(aim, configuration):
        """One capture through the evaluator, cached the way a built room is:
        the geometry behind a given set of controls never changes."""
        return captures.review(aim, configuration)

    @mo.cache
    def nudged(aim, axis, steps, configuration):
        lowest, highest = NUDGE_INCHES
        offsets = [
            to_meters(lowest + (highest - lowest) * step / (steps - 1))
            for step in range(steps)
        ]
        return captures.sweep_shift(aim, axis, offsets, configuration)

    return NUDGE_INCHES, nudged, parked_nudge, surveyed


@app.cell
def _(aim, configuration, destination, origin, surveyed):
    same_stop = origin.value == destination.value
    survey = None if same_stop else surveyed(aim, configuration)
    return same_stop, survey


@app.cell
def _():
    from scenario_sweep_plan import ROUTE_CHECK, plan, runs_of, square_to_the_walls

    return ROUTE_CHECK, plan, runs_of, square_to_the_walls


@app.cell
def _(aim, captures, mo, panels, plan, same_stop, scan, scans, survey):
    def plan_caption(aim):
        capture = captures.capture_named(aim.capture)
        return (
            f"{capture.name} as RoomPlan returned it, with the trip from "
            f"{aim.start.lower()} to {aim.end.lower()} drawn across the floor."
        )

    def plan_view():
        if same_stop:
            return mo.md(
                "A trip needs two different stops. Pick somewhere else to "
                "walk to."
            )
        return mo.Html(plan(aim, survey, plan_caption(aim)))

    mo.vstack(
        [plan_view(), mo.Html(panels.provenance_block(scan.value, scans))], gap=0
    )
    return (plan_view,)


@app.cell
def _(aim, mo, panels, rescan, survey):
    def reading_block(survey):
        return panels.reading_block(aim, survey)

    def withheld_block(survey):
        return panels.withheld_block(survey, rescan.value)

    def reading_view():
        if survey is None:
            return mo.md("")
        return mo.vstack(
            [
                mo.Html(panels.refusal_block(aim)),
                mo.Html(reading_block(survey)),
                mo.Html(withheld_block(survey)),
            ],
            gap=0.9,
        )

    reading_view()
    return reading_block, withheld_block


@app.cell
def _(mo):
    mo.md("""
    ### What moving one piece changes
    """)
    return


@app.cell
def _(mo):
    nudge_steps = mo.ui.slider(
        5, 25, 1, value=13, label="Readings across the range", show_value=True
    )
    nudge_steps
    return (nudge_steps,)


@app.cell
def _(
    NUDGE_INCHES,
    aim,
    captures,
    chart,
    configuration,
    direction,
    distance,
    legend,
    mo,
    nudge_steps,
    nudged,
    panels,
    parked_nudge,
    piece,
    read_sweep,
    survey,
    to_inches,
):
    def nudge_rows():
        axis = direction.value
        sweep = nudged(
            parked_nudge(aim, axis), axis, nudge_steps.value, configuration
        )
        return read_sweep(
            [
                (to_inches(getattr(moved, axis)), result.verdicts)
                for moved, result in sweep
            ]
        )

    def nudge_view():
        if survey is None:
            return mo.md("")
        rows = nudge_rows()
        if not rows:
            return mo.md(panels.nothing_to_draw_note())
        moved_along = captures.SHIFT_AXES[direction.value]
        return mo.vstack(
            [
                mo.md(panels.nudge_note(piece.value, survey, aim)),
                mo.Html(legend(rows)),
                mo.Html(
                    chart(
                        rows,
                        NUDGE_INCHES,
                        distance.value,
                        None,
                        f"{piece.value} moved {moved_along} (inches)",
                        f"What each check said as the {piece.value.lower()} "
                        f"moved {moved_along}.",
                    )
                ),
            ],
            gap=0.5,
        )

    nudge_view()
    return


@app.cell
def _():
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()

