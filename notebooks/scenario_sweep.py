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
    # The workspace tokens, from apps/web/src/app/(workspace)/globals.css. The
    # notebook is a separate runtime, so it restates them here and nowhere else
    # in this file.
    PALETTE = {
        "paper": "#f6f5f1",
        "sheet": "#fcfbf8",
        "rule": "#e3e0d8",
        "ink": "#1b1c1e",
        "muted": "#5d5e61",
        "faint": "#8a8a88",
        "accent": "#2f5e9e",
        "problem": "#c8372d",
        "pass": "#2e7d4f",
    }

    # Each knob that sets a dimension: what to call it, and the range a shop
    # could plausibly be in. The keys are the fields on `Knobs`.
    KNOBS = {
        "aisle_inches": ("Gap between the display cases", 24.0, 72.0, 0.5),
        "counter_inches": ("Counter height", 28.0, 52.0, 0.5),
        "door_inches": ("Front door opening", 26.0, 44.0, 0.5),
    }
    return KNOBS, PALETTE


@app.cell
def _(PALETTE):
    def verdict_colour(verdict):
        """What a verdict looks like, wherever it is drawn."""
        if verdict == "problem":
            return PALETTE["problem"]
        return PALETTE["pass"] if verdict == "passes" else PALETTE["faint"]

    return (verdict_colour,)


@app.cell
def _(KNOBS):
    def bounds(knob):
        return KNOBS[knob][1], KNOBS[knob][2]

    def knob_label(knob):
        return KNOBS[knob][0]

    return bounds, knob_label


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
def _(PALETTE, action_name, mo, outcome, scenarios):
    def share(score):
        return "nothing to score" if score is None else f"{score:.0%}"

    def next_step(outcome):
        return (action_name(outcome) or "nothing").replace("_", " ").lower()

    def figure_block(outcome):
        scored = scenarios.scores(outcome)
        return f"""
        <div style="display:flex;align-items:baseline;gap:2rem;flex-wrap:wrap">
          <div>
            <div style="font-size:3rem;line-height:1;font-variant-numeric:tabular-nums;
                        color:{PALETTE['ink']}">
              {scored['measurement_error_in'] or 0.0:.3f} in</div>
            <div style="font-size:0.875rem;color:{PALETTE['muted']};max-width:24rem;
                        margin-top:0.35rem">
              between the dimensions these sliders set and the dimensions the
              pipeline measured in the room they built.
            </div>
          </div>
          <dl style="margin:0;font-size:0.875rem;color:{PALETTE['muted']};
                     display:grid;grid-template-columns:auto auto;gap:0.3rem 1rem">
            <dt>Questions asked</dt>
            <dd style="margin:0;font-variant-numeric:tabular-nums;
                       color:{PALETTE['ink']}">{share(scored['question_recall'])}</dd>
            <dt>Counter and door found</dt>
            <dd style="margin:0;font-variant-numeric:tabular-nums;
                       color:{PALETTE['ink']}">{share(scored['label_accuracy'])}</dd>
            <dt>What the loop would do next</dt>
            <dd style="margin:0;color:{PALETTE['ink']}">{next_step(outcome)}</dd>
          </dl>
        </div>
        """

    mo.Html(figure_block(outcome))
    return


@app.cell
def _(PALETTE, mo, outcome):
    def finding_card(finding):
        return f"""
        <li style="padding:0.75rem 0.9rem;background:{PALETTE['sheet']};
                   border-radius:0.5rem;
                   box-shadow:inset 3px 0 0 {PALETTE['problem']};
                   margin-bottom:0.4rem;list-style:none">
          <div style="color:{PALETTE['ink']};font-weight:600">{finding.title}</div>
          <div style="color:{PALETTE['muted']};font-size:0.875rem;margin-top:0.2rem">
            {finding.detail}
          </div>
          <div style="color:{PALETTE['faint']};font-size:0.8125rem;margin-top:0.2rem">
            {finding.citation.display()}
          </div>
        </li>
        """

    def nothing_found(outcome):
        if outcome.result.findings:
            return "Every check that ran on this room passed."
        return "No checks are enabled, so nothing has been measured yet."

    def findings_list(outcome):
        problems = outcome.result.problems
        passed = [f for f in outcome.result.findings if f.outcome == "passes"]
        cards = "".join(finding_card(finding) for finding in problems) or (
            f'<p style="color:{PALETTE["muted"]};margin:0">{nothing_found(outcome)}</p>'
        )
        return f"""
        <ul style="padding:0;margin:0">{cards}</ul>
        <p style="color:{PALETTE['faint']};font-size:0.875rem;margin-top:0.6rem">
          {len(passed)} other measurements came back inside their limit.
        </p>
        """

    mo.Html(findings_list(outcome))
    return


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
def _():
    def spans(samples):
        """Merge consecutive readings that said the same thing.

        A verdict is only known at the settings the sweep visited, so a change
        is drawn at the midpoint between the two readings that bracket it.
        """
        runs = []
        for value, verdict in samples:
            if runs and runs[-1][2] == verdict:
                runs[-1][1] = value
            else:
                runs.append([value, value, verdict])
        return [
            (
                first if index == 0 else (runs[index - 1][1] + first) / 2,
                last if index == len(runs) - 1 else (last + runs[index + 1][0]) / 2,
                verdict,
            )
            for index, (first, last, verdict) in enumerate(runs)
        ]

    def read_sweep(readings):
        """Each check, and what it said at every setting a sweep visited.

        `readings` is one (setting, verdicts) pair per reading, so a sweep of
        a fixture dimension and a sweep of a real room's furniture arrive in
        the same shape and draw through the same chart.

        Checks that only ever asked a question are left out. Those are the
        things a scan cannot see, they are the same for every room, and a row
        that never changes is a row that says nothing here.
        """
        said = {}
        for setting, verdicts in readings:
            for check, verdict in verdicts.items():
                said.setdefault(check, []).append((setting, verdict))
        return {
            check: spans(samples)
            for check, samples in said.items()
            if {verdict for _, verdict in samples} - {"question"}
        }

    return (read_sweep,)


@app.cell
def _(load_pack):
    CHART = {
        "width": 720,
        "left": 320,
        "right": 706,
        "top": 48,
        "row": 27,
        "bar": 13,
    }

    TITLES = {rule.id: rule.title for rule in load_pack().rules}

    def rule_title(check):
        return TITLES.get(check, check.replace("_", " "))

    def geometry(count):
        """Where the rows, the baseline and the two label lines sit."""
        baseline = CHART["top"] + count * CHART["row"] + 6
        return {
            **CHART,
            "baseline": baseline,
            "ticks": baseline + 18,
            "caption": baseline + 37,
            "height": baseline + 46,
        }

    def scale(domain):
        lowest, highest = domain
        width = CHART["right"] - CHART["left"]
        return lambda value: CHART["left"] + width * (value - lowest) / (
            highest - lowest
        )

    return geometry, rule_title, scale


@app.cell
def _(PALETTE, rule_title, verdict_colour):
    def band(place, row, verdict, x0, x1):
        top = place["top"] + row * place["row"]
        width = max(x1 - x0 - 2, 1)
        colour = verdict_colour(verdict)
        if verdict == "problem":
            y = top + (place["row"] - place["bar"]) / 2
            return (
                f'<rect x="{x0 + 1:.1f}" y="{y:.1f}" width="{width:.1f}" '
                f'height="{place["bar"]}" rx="4" fill="{colour}" />'
            )
        return (
            f'<rect x="{x0 + 1:.1f}" y="{top + place["row"] / 2 - 1.5:.1f}" '
            f'width="{width:.1f}" height="3" rx="1.5" fill="{colour}" />'
        )

    def row_label(place, index, check, demoted):
        y = place["top"] + index * place["row"] + place["row"] / 2 + 4
        colour = PALETTE["faint"] if demoted else PALETTE["ink"]
        return (
            f'<text x="0" y="{y:.1f}" font-size="13" fill="{colour}">'
            f"{rule_title(check)}</text>"
        )

    def tick_values(domain):
        lowest, highest = domain
        step = 6 if highest - lowest > 24 else 4
        first = step * (int(lowest) // step + 1)
        return [lowest, *range(first, int(highest), step), highest]

    def axis(place, domain, at, title):
        ticks = "".join(
            f'<text x="{at(value):.1f}" y="{place["ticks"]:.1f}" font-size="12" '
            f'fill="{PALETTE["faint"]}" text-anchor="middle" '
            f'style="font-variant-numeric:tabular-nums">{value:g}</text>'
            for value in tick_values(domain)
        )
        middle = (place["left"] + place["right"]) / 2
        return (
            f'<line x1="{place["left"]}" y1="{place["baseline"]:.1f}" '
            f'x2="{place["right"]}" y2="{place["baseline"]:.1f}" '
            f'stroke="{PALETTE["rule"]}" stroke-width="1" />{ticks}'
            f'<text x="{middle:.1f}" y="{place["caption"]:.1f}" font-size="12" '
            f'fill="{PALETTE["muted"]}" text-anchor="middle">{title}</text>'
        )

    return axis, band, row_label


@app.cell
def _(PALETTE, axis, band, geometry, row_label, scale):
    def ordered(rows):
        """What changes first, then what is always wrong, then what is fine."""
        def rank(row):
            check, spans = row
            verdicts = {verdict for _, _, verdict in spans}
            return (len(spans) == 1, "problem" not in verdicts, check)

        return sorted(rows.items(), key=rank)

    def guide(place, value, at, colour, y, label, dashes):
        """A reference line, haloed in the page colour so it stays legible
        where it crosses a bar."""
        x = at(value)
        line = (
            f'<line x1="{x:.1f}" y1="{place["top"] - 6:.1f}" x2="{x:.1f}" '
            f'y2="{place["baseline"]:.1f}" stroke="%s" stroke-width="%s" %s />'
        )
        return (
            line % (PALETTE["paper"], "4", "")
            + line % (colour, "1.5", dashes)
            + f'<text x="{x:.1f}" y="{y:.1f}" font-size="12" '
            f'fill="{colour}" text-anchor="middle">{label}</text>'
        )

    def guides(place, at, here, required):
        drawn = guide(
            place, here, at, PALETTE["accent"], place["top"] - 14,
            f"{here:g} in now", 'stroke-dasharray="3 3"',
        )
        if required is None:
            return drawn
        return (
            guide(
                place, required, at, PALETTE["ink"], place["top"] - 30,
                f"{required:g} in asked for", "",
            )
            + drawn
        )

    def chart(rows, domain, here, required, title, description):
        placed = ordered(rows)
        place = geometry(len(placed))
        at = scale(domain)
        bands = "".join(
            band(place, index, verdict, at(x0), at(x1))
            for index, (_, spans) in enumerate(placed)
            for x0, x1, verdict in spans
        )
        labels = "".join(
            row_label(place, index, check, demoted(spans))
            for index, (check, spans) in enumerate(placed)
        )
        return (
            f'<svg viewBox="0 0 {place["width"]} {place["height"]:.0f}" '
            f'width="100%" role="img" aria-label="{description}" '
            f'style="max-width:{place["width"]}px;overflow:visible">'
            f"<title>{description}</title>{bands}{labels}"
            f"{guides(place, at, here, required if inside(required, domain) else None)}"
            f"{axis(place, domain, at, title)}</svg>"
        )

    def demoted(spans):
        return len(spans) == 1 and spans[0][2] != "problem"

    def inside(value, domain):
        return value is not None and domain[0] <= value <= domain[1]

    return (chart,)


@app.cell
def _(PALETTE, verdict_colour):
    LEGEND = (
        ("Reported a problem", PALETTE["problem"], 6),
        ("Inside its limit", PALETTE["pass"], 3),
        ("Nothing to measure", PALETTE["faint"], 3),
    )

    def legend(rows):
        """Only the marks the chart in front of it actually drew."""
        shown = {
            verdict_colour(verdict)
            for spans in rows.values()
            for _, _, verdict in spans
        }
        marks = "".join(
            f'<span style="display:inline-flex;align-items:center;gap:0.4rem">'
            f'<span style="width:18px;height:{height}px;border-radius:3px;'
            f'background:{colour}"></span>{name}</span>'
            for name, colour, height in LEGEND
            if colour in shown
        )
        return (
            f'<div style="display:flex;gap:1.5rem;font-size:0.875rem;'
            f'color:{PALETTE["muted"]};margin-bottom:0.5rem;flex-wrap:wrap">'
            f"{marks}</div>"
        )

    return (legend,)


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
def _(PALETTE, mo, nothing_verified, outcome, preview, rule_title):
    def open_items(outcome):
        questions = "".join(
            f'<li style="margin-bottom:0.3rem">{finding.title}<span '
            f'style="color:{PALETTE["faint"]}"> — {finding.detail}</span></li>'
            for finding in outcome.result.questions
        )
        waiting = "".join(
            f'<li style="margin-bottom:0.3rem">{rule_title(check)}<span '
            f'style="color:{PALETTE["faint"]}"> — waiting on {reason}</span></li>'
            for check, reason in sorted(outcome.result.held.items())
        )
        return f"""
        <div style="font-size:0.9375rem;color:{PALETTE['muted']}">
          <p style="color:{PALETTE['ink']};margin:0 0 0.4rem">
            Things a scan cannot see, which somebody has to answer.
          </p>
          <ul style="margin:0 0 1rem;padding-left:1.1rem">{questions}</ul>
          <p style="color:{PALETTE['ink']};margin:0 0 0.4rem">
            Rules this room did not settle.
          </p>
          <ul style="margin:0;padding-left:1.1rem">{waiting}</ul>
        </div>
        """

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

    mo.vstack([mo.Html(open_items(outcome)), preview, reviewer_note], gap=0.75)
    return


@app.cell
def _():
    import math

    from standardphysics_agents.evaluation import captures
    from standardphysics_contracts import to_inches, to_meters
    from standardphysics_pipeline.footprints import (
        floor_polygon,
        footprint,
        rotation_about_z,
    )

    return (
        captures,
        floor_polygon,
        footprint,
        math,
        rotation_about_z,
        to_inches,
        to_meters,
    )


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
def _(floor_polygon, footprint, math, rotation_about_z):
    PLAN = {"width": 720, "height": 480, "pad": 30, "foot": 24, "wall": 4.0}
    """`wall` is a drawing weight in pixels, not a measurement.

    RoomPlan returns walls and doorways as zero-thickness planes, so the scan
    has no thickness to draw. Every wall gets the same weight, which keeps the
    drawing from implying one."""

    def outline_of(node):
        """The node's floor shape. RoomPlan floors are rotated shells whose
        dimensions do not say where the floor is, so they get the hull."""
        return floor_polygon(node) if node.kind == "floor" else footprint(node)

    def square_to_the_walls(graph):
        """How far to turn the drawing so the longest wall lies flat.

        A capture is oriented to wherever the phone was standing, which puts
        every room on a slant. Turning by the longest wall costs nothing and
        makes a plan readable, and the smallest turn that squares it is the
        one within 45 degrees.
        """
        walls = [node for node in graph.nodes if node.kind == "wall"]
        if not walls:
            return 0.0
        longest = max(walls, key=lambda node: max(node.dimensions.x, node.dimensions.y))
        cos_t, sin_t = rotation_about_z(longest)
        turn = math.atan2(sin_t, cos_t) % (math.pi / 2)
        return turn if turn < math.pi / 4 else turn - math.pi / 2

    def turned(angle):
        cos_t, sin_t = math.cos(-angle), math.sin(-angle)
        return lambda x, y: (x * cos_t - y * sin_t, x * sin_t + y * cos_t)

    def room_extent(graph, flat):
        """The floor area the scan covers, over every wall and piece on it."""
        corners = [
            flat(x, y) for node in graph.nodes for x, y in outline_of(node)
        ]
        return (
            min(x for x, _ in corners),
            min(y for _, y in corners),
            max(x for x, _ in corners),
            max(y for _, y in corners),
        )

    def plan_frame(graph):
        """Metres to pixels, the room squared, centred, and north-ish up."""
        flat = turned(square_to_the_walls(graph))
        left, bottom, right, top = room_extent(graph, flat)
        across, deep = max(right - left, 0.1), max(top - bottom, 0.1)
        room = PLAN["width"] - 2 * PLAN["pad"]
        tall = PLAN["height"] - 2 * PLAN["pad"] - PLAN["foot"]
        scale = min(room / across, tall / deep)
        return {
            **PLAN,
            "scale": scale,
            "flat": flat,
            "left": PLAN["pad"] + (room - across * scale) / 2 - left * scale,
            "top": PLAN["pad"] + (tall - deep * scale) / 2 + top * scale,
        }

    def on_plan(frame):
        """World metres to pixels. Screen y grows downward, so north is up."""

        def place(x, y):
            flat_x, flat_y = frame["flat"](x, y)
            return (
                frame["left"] + flat_x * frame["scale"],
                frame["top"] - flat_y * frame["scale"],
            )

        return place

    def closed_path(points, at):
        drawn = "".join(
            f"{'M' if index == 0 else 'L'}{x:.1f} {y:.1f}"
            for index, (x, y) in enumerate(at(px, py) for px, py in points)
        )
        return f"{drawn}Z"

    return closed_path, on_plan, outline_of, plan_frame


@app.cell
def _(PALETTE, closed_path, outline_of):
    def haloed(text_svg):
        """Text that stays readable where it crosses a piece of furniture."""
        return text_svg.replace(
            "<text ",
            f'<text stroke="{PALETTE["paper"]}" stroke-width="3" '
            f'paint-order="stroke" ',
        )

    def filled(node, at, fill):
        return f'<path d="{closed_path(outline_of(node), at)}" fill="{fill}" />'

    def wall_line(node, frame, at):
        return (
            f'<path d="{closed_path(outline_of(node), at)}" fill="none" '
            f'stroke="{PALETTE["muted"]}" stroke-width="{frame["wall"]}" '
            f'stroke-linecap="butt" />'
        )

    def way_in(node, frame, at):
        """A door or an opening, cut out of the wall it was found in."""
        line = closed_path(outline_of(node), at)
        return (
            f'<path d="{line}" fill="none" stroke="{PALETTE["sheet"]}" '
            f'stroke-width="{frame["wall"] + 2:.1f}" />'
            f'<path d="{line}" fill="none" stroke="{PALETTE["rule"]}" '
            f'stroke-width="1.25" />'
        )

    def piece_shape(node, at, stroke, weight):
        return (
            f'<path d="{closed_path(outline_of(node), at)}" '
            f'fill="{PALETTE["paper"]}" stroke="{stroke}" '
            f'stroke-width="{weight}" />'
        )

    def ghost(node, at):
        """Where a piece stood before it was nudged."""
        return (
            f'<path d="{closed_path(outline_of(node), at)}" fill="none" '
            f'stroke="{PALETTE["faint"]}" stroke-width="1" '
            f'stroke-dasharray="3 3" />'
        )

    def runs_of(points, inside):
        """The path split where it crosses on or off the scanned floor.

        Consecutive steps that agree become one run, and the run keeps the
        step that ended it so the two pieces meet rather than leaving a gap.
        """
        runs = []
        for step, on_floor in zip(points, inside):
            if runs and runs[-1][0] == on_floor:
                runs[-1][1].append(step)
                continue
            if runs:
                runs[-1][1].append(step)
            runs.append((on_floor, [step]))
        return [(on_floor, steps) for on_floor, steps in runs if len(steps) > 1]

    def trace(steps, at, colour, dashes):
        drawn = " ".join(
            f"{x:.1f},{y:.1f}" for x, y in (at(each.x, each.y) for each in steps)
        )
        line = (
            f'<polyline points="{drawn}" fill="none" stroke="%s" '
            f'stroke-width="%s" %s />'
        )
        return line % (PALETTE["sheet"], "6", "") + line % (colour, "2", dashes)

    def walked(points, inside, at):
        """The route the checks measured along.

        The stretch that left the scanned floor is drawn as the aside it is:
        it is where the widest path escaped through an opening, and colouring
        it like the rest would present a walk around the building as the trip.
        """
        if len(points) < 2:
            return ""
        return "".join(
            trace(
                steps,
                at,
                PALETTE["accent"] if on_floor else PALETTE["faint"],
                'stroke-dasharray="7 5"' if on_floor else 'stroke-dasharray="2 4"',
            )
            for on_floor, steps in runs_of(points, inside)
        )

    def stop_mark(name, node, at):
        x, y = at(node.transform.position.x, node.transform.position.y)
        return (
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" '
            f'fill="{PALETTE["ink"]}" stroke="{PALETTE["paper"]}" '
            f'stroke-width="2" />'
            + haloed(
                f'<text x="{x:.1f}" y="{y - 12:.1f}" font-size="12.5" '
                f'fill="{PALETTE["ink"]}" text-anchor="middle">{name}</text>'
            )
        )

    return (
        filled,
        ghost,
        haloed,
        piece_shape,
        stop_mark,
        walked,
        wall_line,
        way_in,
    )


@app.cell
def _(PALETTE, haloed):
    def pinch_mark(ends, at, colour, label):
        """The two points a width was measured between, and the number."""
        (ax, ay), (bx, by) = at(ends[0].x, ends[0].y), at(ends[1].x, ends[1].y)
        rule = (
            f'<line x1="{ax:.1f}" y1="{ay:.1f}" x2="{bx:.1f}" y2="{by:.1f}" '
            f'stroke="%s" stroke-width="%s" />'
        )
        dots = "".join(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.5" fill="{colour}" />'
            for x, y in ((ax, ay), (bx, by))
        )
        return (
            rule % (PALETTE["sheet"], "6")
            + rule % (colour, "2.25")
            + dots
            + haloed(
                f'<text x="{(ax + bx) / 2:.1f}" y="{(ay + by) / 2 - 9:.1f}" '
                f'font-size="13" font-weight="600" fill="{colour}" '
                f'text-anchor="middle" '
                f'style="font-variant-numeric:tabular-nums">{label}</text>'
            )
        )

    def scale_bar(frame):
        """One metre, so the drawing says how big the room is."""
        y = frame["height"] - frame["foot"] / 2
        end = frame["pad"] + frame["scale"]
        return (
            f'<line x1="{frame["pad"]}" y1="{y:.1f}" x2="{end:.1f}" '
            f'y2="{y:.1f}" stroke="{PALETTE["muted"]}" stroke-width="1.5" />'
            f'<text x="{end + 7:.1f}" y="{y + 4:.1f}" font-size="12" '
            f'fill="{PALETTE["muted"]}">1 m</text>'
        )

    return pinch_mark, scale_bar


@app.cell
def _(
    PALETTE,
    captures,
    filled,
    ghost,
    on_plan,
    piece_shape,
    pinch_mark,
    plan_frame,
    scale_bar,
    stop_mark,
    verdict_colour,
    walked,
    wall_line,
    way_in,
):
    ROUTE_CHECK = "route_clear_width"

    CUT_INTO_A_WALL = ("door", "opening", "window")

    def shell(graph, frame, at):
        return "".join(
            [
                *[
                    filled(node, at, PALETTE["sheet"])
                    for node in graph.nodes
                    if node.kind == "floor"
                ],
                *[
                    wall_line(node, frame, at)
                    for node in graph.nodes
                    if node.kind == "wall"
                ],
                *[
                    way_in(node, frame, at)
                    for node in graph.nodes
                    if node.kind in CUT_INTO_A_WALL
                ],
            ]
        )

    def piece_stroke(picked, rejected):
        """A picked piece takes the accent, or the problem colour once the
        constraints would throw the rearrangement out."""
        if not picked:
            return PALETTE["faint"]
        return PALETTE["problem"] if rejected else PALETTE["accent"]

    def furniture(graph, at, moved, rejected):
        """Every piece, with the one being moved picked out of the rest."""
        picked = moved.id if moved else None
        return "".join(
            piece_shape(
                node,
                at,
                piece_stroke(node.id == picked, rejected),
                2.0 if node.id == picked else 1.0,
            )
            for node in graph.nodes
            if node.kind == "object"
        )

    def what_was_measured(graph, survey, at):
        path = captures.walked_path(survey)
        route = walked(path, captures.on_the_floor(graph, path), at)
        ends = captures.pinch_line(survey, ROUTE_CHECK)
        inches = captures.measured(survey, ROUTE_CHECK)
        if ends is None or inches is None:
            return route
        colour = verdict_colour(survey.verdicts.get(ROUTE_CHECK))
        return route + pinch_mark(ends, at, colour, f"{inches:.1f} in")

    def displaced(aim):
        """The piece as the scan found it, when a nudge has moved it since."""
        if not (aim.shift_x or aim.shift_y):
            return None
        return captures.movable(captures.load(aim.capture)).get(aim.moved)

    def plan(aim, survey, description):
        graph = captures.room(aim)
        frame = plan_frame(graph)
        at = on_plan(frame)
        named = captures.anchors(graph)
        moved = captures.movable(graph).get(aim.moved)
        was = displaced(aim)
        stops = "".join(
            stop_mark(name, named[name], at)
            for name in (aim.start, aim.end)
            if name in named
        )
        return (
            f'<svg viewBox="0 0 {frame["width"]} {frame["height"]}" '
            f'width="100%" role="img" aria-label="{description}" '
            f'style="max-width:{frame["width"]}px">'
            f"<title>{description}</title>"
            f"{shell(graph, frame, at)}"
            f"{ghost(was, at) if was else ''}"
            f"{furniture(graph, at, moved, bool(captures.refused(aim)))}"
            f"{what_was_measured(graph, survey, at)}"
            f"{stops}{scale_bar(frame)}</svg>"
        )

    return ROUTE_CHECK, plan


@app.cell
def _(PALETTE, aim, captures, mo, plan, same_stop, scan, scans, survey):
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

    def provenance_row(label, value):
        return (
            f'<dt style="color:{PALETTE["muted"]}">{label}</dt>'
            f'<dd style="margin:0;color:{PALETTE["ink"]};'
            f'font-variant-numeric:tabular-nums">{value}</dd>'
        )

    def provenance_block():
        capture = next(each for each in scans if each.id == scan.value)
        graph = captures.load(scan.value)
        rows = "".join(
            [
                provenance_row("Capture", capture.provenance()),
                provenance_row("Pieces the scan found", len(captures.anchors(graph))),
                provenance_row(
                    "Only glimpsed",
                    f"{len(captures.unsure(graph))} of {len(graph.nodes)}",
                ),
            ]
        )
        return (
            f'<dl style="margin:0.75rem 0 0;font-size:0.875rem;display:grid;'
            f'grid-template-columns:auto auto;gap:0.3rem 1rem;'
            f'justify-content:start">{rows}</dl>'
        )

    mo.vstack([plan_view(), mo.Html(provenance_block())], gap=0)
    return


@app.cell
def _(PALETTE, ROUTE_CHECK, aim, captures, mo, rescan, rule_title, survey):
    STRAY_ENOUGH_TO_SAY = 0.05
    """Below this the path clipped a doorway, which is not worth a sentence."""

    def tightest(survey):
        inches = captures.measured(survey, ROUTE_CHECK)
        return "not measured" if inches is None else f"{inches:.1f} in"

    def requirement(survey):
        for finding in survey.findings:
            if finding.check_id == ROUTE_CHECK and finding.required_inches:
                return f"{finding.required_inches:g} in is the minimum"
        return "no threshold was read for it"

    def off_the_floor(aim, survey):
        graph = captures.room(aim)
        return captures.strayed(graph, captures.walked_path(survey))

    def what_the_number_is(aim, survey):
        """What the measurement describes, which depends on where it was taken.

        A capture whose walls do not close leaves open ground outside the
        building, and the widest path will use it. When most of the trip ran
        out there, the number is the width of that detour and calling it the
        tightest point of a walk through the room would be wrong.
        """
        strayed = off_the_floor(aim, survey)
        if strayed < STRAY_ENOUGH_TO_SAY:
            return (
                f"at the tightest point of this trip, measured between the two "
                f"pieces the scan found there. {requirement(survey)}."
            )
        return (
            f"at the tightest point of a route that spent {strayed:.0%} of its "
            f"length off the scanned floor. This capture's walls do not close, "
            f"so the widest path went around the outside rather than through "
            f"the room, and the drawing above dots that stretch."
        )

    def reading_block(survey):
        return f"""
        <div>
          <div style="font-size:3rem;line-height:1;
                      font-variant-numeric:tabular-nums;color:{PALETTE['ink']}">
            {tightest(survey)}</div>
          <div style="font-size:0.875rem;color:{PALETTE['muted']};
                      max-width:30rem;margin-top:0.35rem">
            {what_the_number_is(aim, survey)}
          </div>
        </div>
        """

    def next_move(rescan_is_on):
        if rescan_is_on:
            return "The switch is on, so these are the readings a cleared scan gives."
        return "Turn the switch on to see what they would have said."

    def withheld_block(survey):
        waiting = captures.withheld(survey)
        if not waiting:
            return ""
        listed = "".join(
            f'<li style="margin-bottom:0.2rem">{rule_title(check)}'
            f'<span style="color:{PALETTE["faint"]}"> — {asked}</span></li>'
            for check, asked in sorted(waiting.items())
        )
        return f"""
        <div style="font-size:0.9375rem;color:{PALETTE['muted']};max-width:32rem">
          <p style="color:{PALETTE['ink']};margin:0 0 0.4rem">
            {len(waiting)} checks measured something and asked for another
            look rather than ruling on it.
          </p>
          <ul style="margin:0 0 0.5rem;padding-left:1.1rem">{listed}</ul>
          <p style="margin:0">{next_move(rescan.value)}</p>
        </div>
        """

    def refusal_block(aim):
        breaches = captures.refused(aim)
        if not breaches:
            return ""
        listed = "".join(f"<li>{each}</li>" for each in breaches)
        return f"""
        <div style="padding:0.75rem 0.9rem;background:{PALETTE['sheet']};
                    box-shadow:inset 3px 0 0 {PALETTE['problem']};
                    font-size:0.9375rem;max-width:34rem">
          <div style="color:{PALETTE['ink']};font-weight:600">
            The fix agent would throw this rearrangement out
          </div>
          <ul style="margin:0.3rem 0 0;padding-left:1.1rem;
                     color:{PALETTE['muted']}">{listed}</ul>
        </div>
        """

    def reading_view():
        if survey is None:
            return mo.md("")
        return mo.vstack(
            [
                mo.Html(refusal_block(aim)),
                mo.Html(reading_block(survey)),
                mo.Html(withheld_block(survey)),
            ],
            gap=0.9,
        )

    reading_view()
    return


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

    def rests_on_the_piece():
        """Whether this trip's answers are measured against the moved piece."""
        return piece.value in captures.implicated(survey, captures.room(aim))

    def other_pieces():
        named = captures.implicated(survey, captures.room(aim))
        if not named:
            return "No movable piece carries an answer on this trip."
        return f"The answers here rest on {', '.join(sorted(named))}."

    def nudge_note():
        if rests_on_the_piece():
            return (
                "Each row is a check. Its bar covers the distances it "
                "reported a problem at."
            )
        return (
            f"Nothing on this trip is measured against the "
            f"{piece.value.lower()}, so every row stays flat while it moves. "
            f"{other_pieces()}"
        )

    def nothing_to_draw():
        return mo.md(
            "Every check on this trip is waiting on the scan's confidence, so "
            "there is no verdict to draw yet. Turn on the switch above and the "
            "rows appear."
        )

    def nudge_view():
        if survey is None:
            return mo.md("")
        rows = nudge_rows()
        if not rows:
            return nothing_to_draw()
        moved_along = captures.SHIFT_AXES[direction.value]
        return mo.vstack(
            [
                mo.md(nudge_note()),
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
