"""The top-down plan of a scanned room for the scenario sweep notebook: the
room squared to its longest wall, the furniture, the route the checks walked and
the width they measured along it. The notebook's cells pass in the aim and the
survey; nothing here reads marimo state."""

import math

from scenario_sweep_charts import PALETTE, verdict_colour
from standardphysics_agents.evaluation import captures
from standardphysics_pipeline.footprints import floor_polygon, footprint, rotation_about_z

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
    for step, on_floor in zip(points, inside, strict=False):
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
