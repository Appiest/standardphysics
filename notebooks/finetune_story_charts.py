"""The charts of the fine-tuning story: each run's score after every training stage,
the table behind them, the base-against-last comparison, the reward curves and the
loop results."""

from html import escape

from finetune_story_figures import svg, text
from finetune_story_ledger import evaluation_named, last_evaluation, percent

# What each held-out benchmark measures. Reward is on its own 0 to 1 scale; the rest are shares.
METRICS = {
    "cleared": "Cleared every fixable problem",
    "accepted": "Answer accepted by the checker",
    "rules_pass": "Broke no hard rule",
    "reward": "Mean reward",
}

def value_text(value, metric):
    return f"{value:.3f}" if metric == "reward" else percent(value)

def change_text(delta, metric):
    return f"{delta:+.3f}" if metric == "reward" else f"{delta * 100:+.1f} pts"

def change_css(delta):
    if delta > 0.0005:
        return "delta-up"
    return "delta-down" if delta < -0.0005 else "delta-flat"

def stage_changes(run, metric):
    """Each stage's score next to the change from the stage before it."""
    stages, previous = [], None
    for entry in run["evaluations"]:
        value = entry[metric]
        delta = None if previous is None else value - previous
        stages.append({"label": entry["label"], "value": value, "delta": delta, "samples": entry["samples"]})
        previous = value
    return stages


LADDER_WIDTH, LADDER_HEIGHT, LADDER_PAD = 320, 150, 30

def ladder_points(stages):
    step = (LADDER_WIDTH - 2 * LADDER_PAD) / max(len(stages) - 1, 1)
    span = LADDER_HEIGHT - 2 * LADDER_PAD
    return [(LADDER_PAD + index * step, LADDER_HEIGHT - LADDER_PAD - stage["value"] * span)
            for index, stage in enumerate(stages)]

def ladder_marks(stages, points, metric):
    marks = []
    for stage, (x, y) in zip(stages, points):
        css = "dot-base" if stage["delta"] is None else f"dot-stage {change_css(stage['delta'])}"
        marks.append(f'<circle class="{css}" cx="{x:.1f}" cy="{y:.1f}" r="6"/>')
        marks.append(text(x, y - 14, value_text(stage["value"], metric), "label-figure", "middle"))
        marks.append(text(x, LADDER_HEIGHT - 8, stage["label"].replace("After ", ""), "label-small", "middle"))
    return marks

def stage_ladder(run, metric):
    stages = stage_changes(run, metric)
    points = ladder_points(stages)
    path = " ".join(f"{'M' if index == 0 else 'L'} {x:.1f} {y:.1f}" for index, (x, y) in enumerate(points))
    body = (
        f'<line class="axis" x1="{LADDER_PAD - 14}" x2="{LADDER_WIDTH - LADDER_PAD + 14}" '
        f'y1="{LADDER_HEIGHT - LADDER_PAD}" y2="{LADDER_HEIGHT - LADDER_PAD}"/>'
        f'<path class="ladder-line" d="{path}"/>' + "".join(ladder_marks(stages, points, metric))
    )
    return svg(LADDER_WIDTH, LADDER_HEIGHT, f"{run['title']}: {metric} after each training stage",
               ", ".join(f"{stage['label']} {value_text(stage['value'], metric)}" for stage in stages),
               body, scrolls=False)

def change_chips(run, metric):
    chips = [
        f'<span class="delta {change_css(stage["delta"])}">{escape(stage["label"])}: '
        f'<span class="figure">{change_text(stage["delta"], metric)}</span></span>'
        for stage in stage_changes(run, metric) if stage["delta"] is not None
    ]
    return f'<div class="deltas">{"".join(chips)}</div>'

def benchmark_card(run, metric):
    return (
        f'<div class="sheet sheet-plain bench-card"><span class="panel-title">{escape(run["title"])}</span>'
        f'{stage_ladder(run, metric)}{change_chips(run, metric)}</div>'
    )

def benchmark_grid(runs, metric):
    cards = "".join(benchmark_card(run, metric) for run in runs if len(run["evaluations"]) > 1)
    return f'<div class="bench-grid">{cards}</div>'


def benchmark_rows(runs):
    rows = []
    for run in runs:
        cleared = stage_changes(run, "cleared")
        for entry, step in zip(run["evaluations"], cleared):
            rows.append({
                "Run": run["title"],
                "Stage": entry["label"],
                "Answers graded": entry["samples"],
                "Cleared %": round(entry["cleared"] * 100, 1),
                "Change in cleared, pts": None if step["delta"] is None else round(step["delta"] * 100, 1),
                "Accepted %": round(entry["accepted"] * 100, 1),
                "Broke no hard rule %": round(entry["rules_pass"] * 100, 1),
                "Mean reward": round(entry["reward"], 3),
            })
    return rows


def dumbbell(runs):
    compared = [run for run in runs if evaluation_named(run, "Base")]
    width, row, left, right, top = 1180, 54, 250, 170, 44
    height = top + row * len(compared) + 20
    x_of = lambda share: left + share * (width - left - right)
    body = []
    for tick in (0, 0.25, 0.5, 0.75, 1.0):
        body.append(f'<line class="axis" x1="{x_of(tick):.1f}" x2="{x_of(tick):.1f}" y1="{top - 14}" y2="{height - 10}"/>')
        body.append(text(x_of(tick), top - 22, f"{tick * 100:.0f}%", "label-figure", "middle"))
    for index, run in enumerate(compared):
        y = top + row * index + row / 2
        base, final = evaluation_named(run, "Base"), last_evaluation(run)
        final_css = "node-rl" if final["label"] == "After RL" else "node-sft"
        body.append(text(left - 24, y + 5, run["title"], "label label-strong", "end"))
        body.append(f'<line class="dumbbell-link" x1="{x_of(base["cleared"]):.1f}" x2="{x_of(final["cleared"]):.1f}" '
                    f'y1="{y}" y2="{y}"/>')
        body.append(f'<circle class="dot-base" cx="{x_of(base["cleared"]):.1f}" cy="{y}" r="8"/>')
        body.append(f'<circle class="{final_css}" style="animation:none" cx="{x_of(final["cleared"]):.1f}" cy="{y}" r="8"/>')
        body.append(text(width - right + 24, y + 5, f"{percent(base['cleared'])} → {percent(final['cleared'])}",
                         "label-figure"))
    return svg(width, height, "Share of held-out answers that cleared every fixable problem, base model against the last trained model",
               "One row per run. The hollow dot is the base model, the filled dot is the last model that run trained.",
               "".join(body))

def acceptance_moves(runs):
    """How many runs' last adapter got more, and fewer, answers past the checker than the base model did."""
    pairs = [(evaluation_named(run, "Base")["accepted"], last_evaluation(run)["accepted"])
             for run in runs if evaluation_named(run, "Base")]
    return {"compared": len(pairs), "rose": sum(after > before for before, after in pairs),
            "fell": sum(after < before for before, after in pairs)}


def reward_chart(runs):
    width, height, left, right, top, bottom = 1180, 420, 60, 250, 30, 50
    curves = [run for run in runs if run["rl_curve"]]
    longest = max(len(run["rl_curve"]) for run in curves) - 1
    x_of = lambda step: left + step / longest * (width - left - right)
    y_of = lambda reward: height - bottom - reward * (height - top - bottom)
    body = []
    for tick in (0, 0.25, 0.5, 0.75, 1.0):
        css = "axis-strong" if tick == 0 else "axis"
        body.append(f'<line class="{css}" x1="{left}" x2="{width - right}" y1="{y_of(tick):.1f}" y2="{y_of(tick):.1f}"/>')
        body.append(text(left - 12, y_of(tick) + 5, f"{tick:.2f}", "label-figure", "end"))
    for step in range(0, longest + 1, 4):
        body.append(text(x_of(step), height - bottom + 26, step, "label-figure", "middle"))
    body.append(text(width - right, height - 6, "RL step", "label-small", "end"))
    for run in curves:
        points = [(x_of(point["step"]), y_of(point["reward"])) for point in run["rl_curve"]]
        path = " ".join(f"{'M' if index == 0 else 'L'} {x:.1f} {y:.1f}" for index, (x, y) in enumerate(points))
        end_x, end_y = points[-1]
        body.append(
            f'<g class="reward-group"><title>{run["title"]}: {len(points)} steps</title>'
            f'<path class="reward-line" d="{path}"/>'
            f'<circle class="reward-dot" cx="{end_x:.1f}" cy="{end_y:.1f}" r="4"/>'
            f'{text(end_x + 10, end_y + 5, run["title"], "label")}</g>'
        )
    return svg(width, height, "Mean reward per RL step in every run that did RL",
               f"{len(curves)} runs, up to {longest + 1} steps each", "".join(body))


def loop_bars(groups):
    width, row, left, right = 1180, 40, 300, 150
    rows = [(heading, label, result) for heading, results in groups for label, result in results.items()]
    height = 20 + row * len(rows) + 48 * len(groups)
    body, y = [], 10
    for heading, results in groups:
        body.append(text(0, y + 18, heading, "label label-strong"))
        y += 36
        best = max(result["cleared"] for result in results.values())
        for label, result in results.items():
            share = result["cleared"] / result["of"]
            span = width - left - right
            css = "loop-bar loop-bar-best" if result["cleared"] == best else "loop-bar"
            body.append(text(left - 20, y + 20, label, "label", "end"))
            body.append(f'<rect class="loop-track" x="{left}" y="{y + 6}" width="{span}" height="20" rx="10"/>')
            body.append(f'<rect class="{css}" x="{left}" y="{y + 6}" width="{span * share:.1f}" height="20" rx="10"/>')
            body.append(text(width - right + 20, y + 21, f"{result['cleared']} of {result['of']}", "label-figure"))
            y += row
        y += 12
    return svg(width, height, "Rooms cleared within five tries, with and without the solver",
               escape("; ".join(f"{label}: {r['cleared']} of {r['of']}" for _, res in groups for label, r in res.items())),
               "".join(body))
