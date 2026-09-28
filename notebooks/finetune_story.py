import marimo

__generated_with = "0.24.2"
app = marimo.App(width="full", app_title="Fine-tuning Qwen 3.8 27B", css_file="finetune_story.css")


@app.cell
def _():
    import datetime
    import json
    import zlib
    from html import escape

    import marimo as mo

    return datetime, escape, json, mo, zlib


@app.cell
def _(json, mo):
    # Written by scripts/finetune_ledger.py on compute-box. Every number on this page
    # comes from this file; the notebook only arranges it.
    def read_ledger():
        """A file beside the notebook when run locally, and a URL beside the page in a WebAssembly export."""
        location = mo.notebook_location() / "public" / "finetune_ledger.json"
        try:
            return json.loads(location.read_text())
        except (AttributeError, OSError):
            from pyodide.http import open_url

            return json.loads(open_url(str(location)).read())

    LEDGER = read_ledger()
    RUNS = sorted(LEDGER["runs"], key=lambda run: run["sessions"][0]["opened_at"])
    return LEDGER, RUNS


@app.cell
def _(datetime):
    PACIFIC = datetime.timezone(datetime.timedelta(hours=-7), "Pacific time")

    def pacific(timestamp):
        return datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(PACIFIC)

    def started(run):
        return pacific(run["sessions"][0]["opened_at"])

    def clock(moment):
        return moment.strftime("%-I:%M %p").lower()

    def day(moment):
        return moment.strftime("%a %b %-d")

    return clock, day, pacific, started


@app.cell
def _():
    def percent(share):
        return "none" if share is None else f"{share * 100:.1f}%"

    def millions(count):
        return f"{count / 1_000_000:.1f}M"

    def whole(count):
        return f"{count:,}"

    def rl_rollouts(run):
        training = run["training"]
        return (training.get("rl_steps_done") or 0) * (training.get("rl_rollouts_per_step") or 0)

    def evaluation_named(run, label):
        return next((entry for entry in run["evaluations"] if entry["label"] == label), None)

    def last_evaluation(run):
        return run["evaluations"][-1] if run["evaluations"] else None

    return evaluation_named, last_evaluation, millions, percent, rl_rollouts, whole


@app.cell
def _(LEDGER, RUNS, pacific, rl_rollouts):
    def totals():
        spend = [run["spend"] for run in RUNS]
        first = pacific(LEDGER["adapters"][0]["created"])
        last = pacific(LEDGER["adapters"][-1]["created"])
        return {
            "adapters": len(LEDGER["adapters"]),
            "runs": len(RUNS),
            "sessions": sum(len(run["sessions"]) for run in RUNS),
            "sft_rows": sum(run["training"].get("sft_rows") or 0 for run in RUNS),
            "sft_examples": sum(
                (run["training"].get("sft_rows") or 0) * (run["training"].get("sft_epochs") or 0) for run in RUNS
            ),
            "rl_steps": sum(run["training"].get("rl_steps_done") or 0 for run in RUNS),
            "rl_rollouts": sum(rl_rollouts(run) for run in RUNS),
            "train_tokens": sum(entry["train_tokens"] for entry in spend),
            "read_tokens": sum(entry["prefill_tokens"] + entry["sample_tokens"] for entry in spend),
            "graded": sum(entry["samples"] for run in RUNS for entry in run["evaluations"]),
            "dollars": sum(entry["estimated_dollars"] for entry in spend),
            "first": first,
            "last": last,
        }

    TOTALS = totals()
    return (TOTALS,)


@app.cell
def _(escape, zlib):
    def svg(width, height, title, description, body, css="", scrolls=True):
        """An inline chart that names itself for a screen reader. Wide charts scroll on a phone rather than shrink."""
        key = f"chart-{zlib.crc32(title.encode()):x}"
        frame = "chart-scroll" if scrolls else "chart-frame"
        return (
            f'<div class="{frame}"><svg class="chart {css}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="{key}-t {key}-d">'
            f'<title id="{key}-t">{escape(title)}</title><desc id="{key}-d">{escape(description)}</desc>{body}</svg></div>'
        )

    def text(x, y, content, css="label", anchor="start"):
        return f'<text x="{x:.1f}" y="{y:.1f}" class="{css}" text-anchor="{anchor}">{escape(str(content))}</text>'

    def wrapped(x, y, content, css="label", anchor="middle", width=16, leading=17):
        lines, line = [], ""
        for word in content.split():
            if line and len(line) + len(word) + 1 > width:
                lines.append(line)
                line = word
            else:
                line = f"{line} {word}".strip()
        lines.append(line)
        spans = "".join(
            f'<tspan x="{x:.1f}" dy="{0 if index == 0 else leading}">{escape(part)}</tspan>'
            for index, part in enumerate(lines)
        )
        return f'<text x="{x:.1f}" y="{y:.1f}" class="{css}" text-anchor="{anchor}">{spans}</text>'

    return svg, text, wrapped


@app.cell
def _():
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

    return LINEAGE_HEIGHT, LINEAGE_WIDTH, TRUNK_Y, place


@app.cell
def _():
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

    return graft_path, node, rl_node_css, segment


@app.cell
def _(node, rl_node_css, segment):
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

    return (stem_marks,)


@app.cell
def _(TRUNK_Y, clock, millions, started, text, wrapped):
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

    return stem_labels, stem_note


@app.cell
def _(LINEAGE_WIDTH, TRUNK_Y, day, started, text):
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

    return day_brackets, legend


@app.cell
def _(
    LINEAGE_HEIGHT,
    LINEAGE_WIDTH,
    TRUNK_Y,
    day_brackets,
    escape,
    graft_path,
    legend,
    place,
    stem_labels,
    stem_marks,
    stem_note,
    svg,
    text,
):
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

    return (lineage,)


@app.cell
def _(RUNS, TOTALS, day, lineage, mo):
    mo.Html(f"""
    <div class="story hero">
      <h1>Fine-tuning Qwen&nbsp;3.8&nbsp;27B</h1>
      <p class="lede">From <strong>{day(TOTALS["first"])}</strong> to <strong>{day(TOTALS["last"])}</strong>
      we trained <strong>{TOTALS["adapters"]} LoRA adapters</strong> across <strong>{TOTALS["runs"]} runs</strong>,
      all on the same 27-billion-parameter base model. Each run asked whether a different kind of training data
      would teach it to rearrange a shop until it meets the ADA.</p>
      <div class="sheet">{lineage(RUNS)}</div>
    </div>
    """)
    return


@app.cell
def _(LEDGER, TOTALS, millions, whole):
    def title_cell(figure, caption, lead=False):
        css = "title-cell title-cell-lead" if lead else "title-cell"
        return f'<div class="{css}"><span class="figure">{figure}</span><span class="caption">{caption}</span></div>'

    def title_block():
        rank = LEDGER["adapters"][0]["rank"]
        cells = [
            title_cell(TOTALS["adapters"], f"LoRA adapters promoted on Fireworks, rank {rank}", lead=True),
            title_cell(TOTALS["sessions"], f"training sessions across {TOTALS['runs']} runs"),
            title_cell(whole(TOTALS["sft_rows"]), f"SFT rows, {whole(TOTALS['sft_examples'])} examples counting epochs"),
            title_cell(TOTALS["rl_steps"], f"RL steps, {whole(TOTALS['rl_rollouts'])} scored answers"),
            title_cell(millions(TOTALS["train_tokens"]), "tokens trained on"),
            title_cell(millions(TOTALS["read_tokens"]), "tokens read and written while sampling"),
            title_cell(whole(TOTALS["graded"]), "held-out answers graded by the ADA checker"),
            title_cell(f"${TOTALS['dollars']:.0f}", "estimated training spend, every token billed uncached"),
        ]
        return f'<div class="story section"><div class="title-block">{"".join(cells)}</div></div>'

    return (title_block,)


@app.cell
def _(mo, title_block):
    mo.Html(title_block())
    return


@app.cell
def _(percent):
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

    return METRICS, change_css, change_text, stage_changes, value_text


@app.cell
def _(change_css, change_text, escape, stage_changes, svg, text, value_text):
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

    return (benchmark_grid,)


@app.cell
def _(mo):
    mo.Html(
        '<div class="story section"><h2>Benchmarks after each training stage</h2>'
        "<p>Each card is one run, graded on its own held-out rooms before training, after SFT and after RL. "
        "The chips under it are the change each stage made. Pick a benchmark and marimo re-runs only the cell "
        "that draws the cards.</p></div>"
    )
    return


@app.cell
def _(METRICS, mo):
    metric_picker = mo.ui.radio(
        options={label: key for key, label in METRICS.items()},
        value=METRICS["cleared"],
        inline=True,
        label="Benchmark",
    )
    mo.Html(f'<div class="story picker-row">{metric_picker}</div>')
    return (metric_picker,)


@app.cell
def _(RUNS, benchmark_grid, metric_picker, mo):
    mo.Html(f'<div class="story">{benchmark_grid(RUNS, metric_picker.value)}</div>')
    return


@app.cell
def _(RUNS, stage_changes):
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

    BENCHMARK_ROWS = benchmark_rows(RUNS)
    return (BENCHMARK_ROWS,)


@app.cell
def _(BENCHMARK_ROWS, mo):
    benchmark_table = mo.ui.table(
        BENCHMARK_ROWS,
        selection=None,
        page_size=len(BENCHMARK_ROWS),
        show_column_summaries=False,
        show_data_types=False,
        label="Every graded checkpoint. Sort or search any column.",
    )
    mo.Html(f'<div class="story bench-table">{benchmark_table}</div>')
    return (benchmark_table,)


@app.cell
def _(clock, day, escape, millions, percent, started, whole):
    def funnel_rows(run):
        funnel = run.get("funnel")
        if not funnel:
            return ""
        widest = max(step["count"] for step in funnel)
        rows = "".join(
            f'<div class="bar-row"><span>{escape(step["label"])}</span>'
            f'<div class="bar-track"><div class="bar-fill bar-fill-soft" style="width:{step["count"] / widest * 100:.1f}%">'
            f'</div></div><span class="figure">{whole(step["count"])}</span></div>'
            for step in funnel
        )
        return f'<div class="sheet sheet-plain"><span class="panel-title">Training data</span><div class="bar-rows">{rows}</div></div>'

    def training_facts(run):
        training, spend = run["training"], run["spend"]
        facts = [("Started", f"{day(started(run))}, {clock(started(run))}")]
        if training.get("sft_rows"):
            facts.append(("SFT", f"{whole(training['sft_rows'])} rows × {training['sft_epochs']} epochs"))
        if training.get("rl_steps_done"):
            facts.append(("RL", f"{training['rl_steps_done']} of {training['rl_steps_planned']} steps × "
                                f"{training['rl_rollouts_per_step']} answers"))
        facts += [
            ("Tokens trained on", millions(spend["train_tokens"])),
            ("Estimated spend", f"${spend['estimated_dollars']:.2f}"),
            ("Adapters", "<br>".join(escape(model) for model in run["models"]) or "none promoted"),
        ]
        rows = "".join(f"<dt>{name}</dt><dd>{value}</dd>" for name, value in facts)
        return f'<div class="sheet sheet-plain"><span class="panel-title">Training</span><dl class="facts">{rows}</dl></div>'

    def evaluation_rows(run):
        rows = "".join(
            f'<div class="bar-row"><span>{escape(entry["label"])}</span>'
            f'<div class="bar-track"><div class="bar-fill" style="width:{(entry["cleared"] or 0) * 100:.1f}%"></div></div>'
            f'<span class="figure">{percent(entry["cleared"])}</span></div>'
            for entry in run["evaluations"]
        )
        samples = run["evaluations"][0]["samples"] if run["evaluations"] else 0
        return (
            f'<div class="sheet sheet-plain"><span class="panel-title">Answers that cleared every fixable problem</span>'
            f'<div class="bar-rows">{rows}</div>'
            f'<p class="muted">{escape(run["eval_set"])}, {whole(samples)} answers per model.</p></div>'
        )

    return evaluation_rows, funnel_rows, training_facts


@app.cell
def _(escape, evaluation_rows, funnel_rows, svg, text, training_facts):
    def reward_sparkline(run):
        curve = run["rl_curve"]
        if not curve:
            return ""
        width, height, pad = 420, 150, 14
        last = max(len(curve) - 1, 1)
        points = [(pad + point["step"] / last * (width - 2 * pad), height - pad - point["reward"] * (height - 2 * pad))
                  for point in curve]
        path = " ".join(f"{'M' if index == 0 else 'L'} {x:.1f} {y:.1f}" for index, (x, y) in enumerate(points))
        dots = "".join(f'<circle class="reward-dot" cx="{x:.1f}" cy="{y:.1f}" r="3"/>' for x, y in points)
        body = (
            f'<line class="axis" x1="{pad}" x2="{width - pad}" y1="{height - pad}" y2="{height - pad}"/>'
            f'<line class="axis" x1="{pad}" x2="{width - pad}" y1="{pad}" y2="{pad}"/>'
            f'{text(width - pad, pad - 4, "reward 1.0", "label-small", "end")}'
            f'<g class="reward-group"><path class="reward-line" d="{path}"/>{dots}</g>'
        )
        chart = svg(width, height, f"Mean reward per RL step, {run['title']}",
                    f"{len(curve)} steps from {curve[0]['reward']:.2f} to {curve[-1]['reward']:.2f}", body, scrolls=False)
        return (f'<div class="sheet sheet-plain"><span class="panel-title">Mean reward per RL step</span>{chart}'
                f'<p class="muted">{len(curve)} steps, from {curve[0]["reward"]:.2f} to {curve[-1]["reward"]:.2f}.</p></div>')

    def sources(run):
        items = "".join(f"<li><code>{escape(path)}</code></li>" for path in run["sources"])
        return f'<details class="sources"><summary>Where these numbers come from</summary><ul>{items}</ul></details>'

    def run_panel(run):
        return (
            f'<section class="run-panel" data-run="{run["key"]}" aria-labelledby="title-{run["key"]}">'
            f'<div class="run-head"><h3 id="title-{run["key"]}">{escape(run["title"])}</h3>'
            f'<p class="lede">{escape(run["question"])}</p></div>'
            f'<div class="run-grid">{training_facts(run)}{funnel_rows(run)}{evaluation_rows(run)}'
            f'{reward_sparkline(run)}</div>{sources(run)}</section>'
        )

    return (run_panel,)


@app.cell
def _(day, escape, run_panel, started):
    CHECK_ICON = (
        '<svg class="run-card-check" viewBox="0 0 256 256" aria-hidden="true"><path d="M229.66,77.66l-128,128a8,8,0,0,1'
        '-11.32,0l-56-56a8,8,0,0,1,11.32-11.32L96,188.69,218.34,66.34a8,8,0,0,1,11.32,11.32Z"/></svg>'
    )

    def stage_marks(run):
        training = run["training"]
        marks = ['<span class="mark mark-sft"></span>'] if training.get("sft_rows") else []
        if training.get("rl_steps_done"):
            promoted = run["models"] and run["models"][-1].endswith("-rl")
            marks.append(f'<span class="mark {"mark-rl" if promoted else "mark-unpromoted"}"></span>')
        return f'<span class="run-card-marks" aria-hidden="true">{"".join(marks)}</span>'

    def run_card(run):
        return (
            f'<label class="run-card" for="pick-{run["key"]}">{CHECK_ICON}'
            f'<span class="run-card-title">{escape(run["title"])}</span>'
            f'<span class="run-card-when">{day(started(run))}</span>{stage_marks(run)}</label>'
        )

    def run_picker(runs):
        """Radio buttons and sibling selectors, so choosing a run works in the exported page with no Python behind it."""
        inputs = "".join(
            f'<input type="radio" name="run" id="pick-{run["key"]}" aria-labelledby="title-{run["key"]}"'
            f'{" checked" if index == 0 else ""}>'
            for index, run in enumerate(runs)
        )
        rules = "".join(
            f'#pick-{key}:checked ~ .run-panels [data-run="{key}"] {{ display: grid; }}'
            f'#pick-{key}:checked ~ .run-cards [for="pick-{key}"] {{ box-shadow: var(--ring-selected); }}'
            f'#pick-{key}:checked ~ .run-cards [for="pick-{key}"] .run-card-check {{ opacity: 1; scale: 1; }}'
            f'#pick-{key}:focus-visible ~ .run-cards [for="pick-{key}"] {{ outline: 2px solid var(--ink); outline-offset: 3px; }}'
            for key in (run["key"] for run in runs)
        )
        cards = "".join(run_card(run) for run in runs)
        panels = "".join(run_panel(run) for run in runs)
        return (
            f'<div class="run-picker"><style>{rules}</style>{inputs}'
            f'<div class="run-cards">{cards}</div><div class="run-panels">{panels}</div></div>'
        )

    return (run_picker,)


@app.cell
def _(RUNS, mo, run_picker):
    mo.Html(f'<div class="story section"><h2>The runs</h2>{run_picker(RUNS)}</div>')
    return


@app.cell
def _(RUNS, svg, text):
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

    REWARD_CHART = reward_chart(RUNS)
    return (REWARD_CHART,)


@app.cell
def _(REWARD_CHART, TOTALS, mo, whole):
    mo.Html(f"""
    <div class="story section">
      <h2>Reward during RL</h2>
      <p>Each RL step sampled a batch of answers, scored every one with the ADA checker and trained on the
      difference. That adds up to <span class="figure">{TOTALS["rl_steps"]}</span> steps and
      <span class="figure">{whole(TOTALS["rl_rollouts"])}</span> scored answers. The menu run starts near 1.0
      because every move on its menu has already passed the checker.</p>
      <div class="sheet">{REWARD_CHART}</div>
    </div>
    """)
    return


@app.cell
def _(RUNS, evaluation_named, last_evaluation, percent, svg, text):
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

    CLEARANCE_CHART = dumbbell(RUNS)
    ACCEPTANCE = acceptance_moves(RUNS)
    return ACCEPTANCE, CLEARANCE_CHART


@app.cell
def _(ACCEPTANCE, CLEARANCE_CHART, mo):
    mo.Html(f"""
    <div class="story section">
      <h2>Clearance before and after training</h2>
      <p>The hollow dot is the base model and the filled one is the last adapter the run trained, both graded on
      the run's own held-out rooms with one try per answer. In {ACCEPTANCE["rose"]} of these
      {ACCEPTANCE["compared"]} runs training got more answers past the checker, and in {ACCEPTANCE["fell"]} it got
      fewer. The share that cleared every fixable problem moved much less. Each run used a different held-out
      set, so compare a row with itself, not with the row above it. The two RL ablations started from the
      harness run's SFT adapter and have no base score of their own.</p>
      <div class="sheet">{CLEARANCE_CHART}</div>
    </div>
    """)
    return


@app.cell
def _(RUNS, escape, svg, text):
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

    _night = next(run for run in RUNS if "five_loop" in run)["five_loop"]
    LOOP_CHART = loop_bars([
        ("65 real held-out rooms", _night["real_heldout"]),
        ("105 generated test shops no model trained on", _night["test_shops"]),
    ])
    return (LOOP_CHART,)


@app.cell
def _(LOOP_CHART, mo):
    mo.Html(f"""
    <div class="story section">
      <h2>Finished rooms with and without the solver</h2>
      <p>On September 26 we graded whole loops instead of single answers: the model proposes a rearrangement,
      the checker names what is still wrong, and the model gets four more tries. SFT made single answers
      better but made this loop worse, because the tuned model revised less after feedback. Putting our room
      solver inside the loop cleared far more rooms than any adapter did.</p>
      <div class="sheet">{LOOP_CHART}</div>
    </div>
    """)
    return


@app.cell
def _(LEDGER, clock, day, mo, pacific):
    _collected = pacific(LEDGER["collected_at"])
    mo.Html(f"""
    <div class="story footer muted">
      <p>Collected from the run folders on compute-box and the Fireworks model list on {day(_collected)} at
      {clock(_collected)} Pacific time. Spend is the trainer's own pessimistic estimate, which bills every prompt
      token uncached; Fireworks does not report a billed total per run.</p>
      <p>Refresh the numbers with <code>scripts/finetune_ledger.py</code>, then export with
      <code>marimo export html-wasm notebooks/finetune_story.py -o finetune_story --mode run</code>.</p>
    </div>
    """)
    return


if __name__ == "__main__":
    app.run()
