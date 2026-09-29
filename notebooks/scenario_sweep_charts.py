"""Drawing for the scenario sweep notebook: the palette, the knob ranges, and the
bar chart that shows what each check said as a slider moves. Nothing here reads
marimo state; the notebook's cells pass in what they hold."""

from standardphysics_agents.rules import load_pack

# The workspace tokens, from apps/web/src/app/(workspace)/globals.css. The
# notebook is a separate runtime, so it restates them here and nowhere else
# in its source.
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


def verdict_colour(verdict):
    """What a verdict looks like, wherever it is drawn."""
    if verdict == "problem":
        return PALETTE["problem"]
    return PALETTE["pass"] if verdict == "passes" else PALETTE["faint"]


def bounds(knob):
    return KNOBS[knob][1], KNOBS[knob][2]

def knob_label(knob):
    return KNOBS[knob][0]


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
