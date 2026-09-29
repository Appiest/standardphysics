"""The written-out blocks of the scenario sweep notebook: the headline
measurement, the findings, the open questions and the reading of a scanned
route, each as an HTML string. The cells wrap them in `mo.Html`; nothing here
reads marimo state."""

from scenario_sweep_charts import PALETTE, rule_title
from scenario_sweep_plan import ROUTE_CHECK
from standardphysics_agents.evaluation import captures, scenarios
from standardphysics_agents.evaluation.runner import action_name


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


def provenance_row(label, value):
    return (
        f'<dt style="color:{PALETTE["muted"]}">{label}</dt>'
        f'<dd style="margin:0;color:{PALETTE["ink"]};'
        f'font-variant-numeric:tabular-nums">{value}</dd>'
    )


def provenance_block(scan_id, scans):
    capture = next(each for each in scans if each.id == scan_id)
    graph = captures.load(scan_id)
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

def reading_block(aim, survey):
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

def withheld_block(survey, rescan_is_on):
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
      <p style="margin:0">{next_move(rescan_is_on)}</p>
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


def other_pieces(survey, aim):
    named = captures.implicated(survey, captures.room(aim))
    if not named:
        return "No movable piece carries an answer on this trip."
    return f"The answers here rest on {', '.join(sorted(named))}."


def nudge_note(piece_name, survey, aim):
    """Whether this trip's answers are measured against the moved piece."""
    if piece_name in captures.implicated(survey, captures.room(aim)):
        return (
            "Each row is a check. Its bar covers the distances it "
            "reported a problem at."
        )
    return (
        f"Nothing on this trip is measured against the "
        f"{piece_name.lower()}, so every row stays flat while it moves. "
        f"{other_pieces(survey, aim)}"
    )


def nothing_to_draw_note():
    return (
        "Every check on this trip is waiting on the scan's confidence, so "
        "there is no verdict to draw yet. Turn on the switch above and the "
        "rows appear."
    )
