"""The lineage figure that opens the fine-tuning story: one stem per run, grown from
the base model or from the run it resumed, with height showing tokens trained on."""

from html import escape

from finetune_story_figures import svg, text, wrapped
from finetune_story_ledger import clock, day, millions, started

LINEAGE_WIDTH, LINEAGE_HEIGHT = 1180, 600
TRUNK_Y, TALLEST = 430, 330
MARGIN_LEFT, MARGIN_RIGHT = 180, 30

def stages(run):
    training = run["training"]
    return bool(training.get("sft_rows")), bool(training.get("rl_steps_done"))

def grafted_from(run, heights):
    """A run that resumed another run's saved state grows from that run's SFT or RL node."""
    parent = heights.get(run["parent"])
    if parent is None:
        return None
    source = run["sessions"][0]["from_state"] or ""
    return (run["parent"], "sft" if "sft-state" in source or parent["rl"] is None else "rl")

def token_heights(runs):
    """Each node's height above the trunk, in tokens trained on, counting the run it grew from."""
    heights = {}
    for run in runs:
        graft = grafted_from(run, heights)
        base = heights[graft[0]][graft[1]] if graft else 0
        tokens = run["spend"]["train_tokens"]
        has_sft, has_rl = stages(run)
        done = run["training"].get("rl_steps_done") or 0
        planned = run["training"].get("rl_steps_planned") or 0
        heights[run["key"]] = {
            "graft": graft,
            "base": base,
            "sft": base + (tokens * 0.5 if has_rl else tokens) if has_sft else None,
            "rl": base + tokens if has_rl else None,
            "planned": base + tokens * planned / done if has_rl and planned > done else None,
        }
    return heights

def place(runs):
    heights = token_heights(runs)
    tallest = max(value for entry in heights.values() for value in (entry["rl"], entry["sft"], entry["planned"])
                  if value is not None)
    y_of = lambda tokens: TRUNK_Y - tokens / tallest * TALLEST
    step = (LINEAGE_WIDTH - MARGIN_LEFT - MARGIN_RIGHT) / len(runs)
    placed = {}
    for index, run in enumerate(runs):
        x, entry = MARGIN_LEFT + step * (index + 0.5), heights[run["key"]]
        graft = entry["graft"]
        placed[run["key"]] = {
            "run": run,
            "order": index,
            "x": x,
            "graft": placed[graft[0]][graft[1] + "_node"] if graft else None,
            "base_y": y_of(entry["base"]),
            "sft_node": (x, y_of(entry["sft"])) if entry["sft"] is not None else None,
            "rl_node": (x, y_of(entry["rl"])) if entry["rl"] is not None else None,
            "planned_top": y_of(entry["planned"]) if entry["planned"] is not None else None,
        }
    return placed



def graft_path(spot):
    (from_x, from_y), x = spot["graft"], spot["x"]
    bend = (x - from_x) * 0.55
    return (
        f'<path class="graft" pathLength="1" style="--order:{spot["order"]}" '
        f'd="M {from_x:.1f} {from_y:.1f} C {from_x + bend:.1f} {from_y:.1f} {x - bend:.1f} {spot["base_y"]:.1f} '
        f'{x:.1f} {spot["base_y"]:.1f}"/>'
    )

def segment(css, x, y1, y2, order):
    return (
        f'<line class="stem {css}" pathLength="1" style="--order:{order}" '
        f'x1="{x:.1f}" y1="{y1:.1f}" x2="{x:.1f}" y2="{y2:.1f}"/>'
    )

def node(css, point, order, radius=8):
    return f'<circle class="{css}" style="--order:{order}" cx="{point[0]:.1f}" cy="{point[1]:.1f}" r="{radius}"/>'

def rl_node_css(spot):
    return "node-rl" if spot["run"]["models"] and spot["run"]["models"][-1].endswith("-rl") else "node-unpromoted"


def sft_marks(spot):
    if not spot["sft_node"]:
        return [], []
    stem = segment("stem-sft", spot["x"], spot["base_y"], spot["sft_node"][1], spot["order"])
    return [stem], [node("node-sft", spot["sft_node"], spot["order"])]

def rl_marks(spot):
    if not spot["rl_node"]:
        return [], []
    start = spot["sft_node"][1] if spot["sft_node"] else spot["base_y"]
    stem = segment("stem-rl", spot["x"], start, spot["rl_node"][1], spot["order"])
    return [stem], [node(rl_node_css(spot), spot["rl_node"], spot["order"])]

def unfinished_marks(spot):
    """The RL steps a run still has to go, dotted above its latest checkpoint, which pulses."""
    if spot["planned_top"] is None:
        return [], []
    x, top = spot["x"], spot["rl_node"][1]
    planned = f'<line class="stem-planned" x1="{x:.1f}" y1="{top:.1f}" x2="{x:.1f}" y2="{spot["planned_top"]:.1f}"/>'
    return [planned], [node("node-pulse", spot["rl_node"], spot["order"])]

def stem_marks(spot):
    """Every stem is drawn before any node, so the nodes sit on top of the lines."""
    stems, nodes = [], []
    for marks in (unfinished_marks, sft_marks, rl_marks):
        lines, dots = marks(spot)
        stems += lines
        nodes += dots
    return stems + nodes


def stem_note(spot):
    run, training = spot["run"], spot["run"]["training"]
    parts = [run["title"] + "."]
    if training.get("sft_rows"):
        parts.append(f"SFT on {training['sft_rows']:,} rows for {training['sft_epochs']} epochs.")
    if training.get("rl_steps_done"):
        parts.append(f"{training['rl_steps_done']} of {training['rl_steps_planned']} RL steps.")
    parts.append(f"{millions(run['spend']['train_tokens'])} tokens trained on.")
    parts.append("Adapters: " + (", ".join(run["models"]) or "none promoted") + ".")
    return " ".join(parts)

def stem_labels(spot):
    x, top = spot["x"], min(point[1] for point in (spot["sft_node"], spot["rl_node"]) if point)
    labels = [text(x, top - 16, millions(spot["run"]["spend"]["train_tokens"]), "label-figure", "middle")]
    if spot["planned_top"] is not None:
        training = spot["run"]["training"]
        labels.append(text(x - 14, (spot["rl_node"][1] + spot["planned_top"]) / 2 + 5, f"{training['rl_steps_done']} of "
                           f"{training['rl_steps_planned']} RL steps so far", "label-small", "end"))
    labels.append(wrapped(x, TRUNK_Y + 36, spot["run"]["title"], "label label-strong", width=12, leading=19))
    labels.append(text(x, TRUNK_Y + 104, clock(started(spot["run"])), "label-figure", "middle"))
    return labels


def day_brackets(placed):
    groups, brackets = {}, []
    for spot in placed.values():
        groups.setdefault(day(started(spot["run"])), []).append(spot["x"])
    for label, xs in groups.items():
        left, right, y = min(xs) - 48, max(xs) + 48, TRUNK_Y + 126
        brackets.append(f'<path class="day-bracket" d="M {left:.1f} {y - 6} V {y} H {right:.1f} V {y - 6}"/>')
        brackets.append(text((left + right) / 2, y + 24, label, "label label-strong", "middle"))
    return brackets

def legend():
    y = 34
    items = [("node-sft", "SFT adapter"), ("node-rl", "RL adapter"), ("node-unpromoted", "RL checkpoint, not promoted")]
    marks, x = [], 0
    for css, name in items:
        marks.append(f'<circle class="{css}" style="animation:none" cx="{x + 8}" cy="{y - 5}" r="7"/>')
        marks.append(text(x + 24, y, name, "label"))
        x += 40 + len(name) * 9
    marks.append(text(LINEAGE_WIDTH - 12, y, "Height is tokens trained on", "label-small", "end"))
    return marks


def lineage(runs):
    placed = place(runs)
    spots = list(placed.values())
    body = [f'<line class="trunk" x1="0" y1="{TRUNK_Y}" x2="{LINEAGE_WIDTH}" y2="{TRUNK_Y}"/>']
    body.append(text(0, TRUNK_Y - 38, "Qwen 3.8 27B", "label-base"))
    body.append(text(0, TRUNK_Y - 16, "the base model", "label-small"))
    body += legend()
    body += [graft_path(spot) for spot in spots if spot["graft"]]
    for spot in spots:
        marks = "".join(stem_marks(spot) + stem_labels(spot))
        body.append(f'<g class="run-group"><title>{escape(stem_note(spot))}</title>{marks}</g>')
    body += day_brackets(placed)
    return svg(
        LINEAGE_WIDTH,
        LINEAGE_HEIGHT,
        "Every fine-tuning run, branching off the base model",
        "One stem per run, in the order they started. SFT segments are dark, RL segments amber, and a "
        "run that resumed another run's saved state grows from that run's node.",
        "".join(body),
        css="lineage",
    )
