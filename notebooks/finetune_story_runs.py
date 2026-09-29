"""The per-run panels of the fine-tuning story: what each run trained on, how it
scored, and the picker that switches between runs."""

from html import escape

from finetune_story_figures import svg, text
from finetune_story_ledger import clock, day, millions, percent, started, whole


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
