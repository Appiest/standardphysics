import marimo

__generated_with = "0.24.2"
app = marimo.App(width="full", app_title="Fine-tuning Qwen 3.8 27B", css_file="finetune_story.css")


@app.cell
def _():
    import json

    import marimo as mo

    return json, mo


@app.cell
def _():
    from finetune_story_charts import (
        METRICS,
        acceptance_moves,
        benchmark_grid,
        benchmark_rows,
        dumbbell,
        loop_bars,
        reward_chart,
    )
    from finetune_story_ledger import clock, day, millions, pacific, totals, whole
    from finetune_story_lineage import lineage
    from finetune_story_runs import run_picker

    return (
        METRICS,
        acceptance_moves,
        benchmark_grid,
        benchmark_rows,
        clock,
        day,
        dumbbell,
        lineage,
        loop_bars,
        millions,
        pacific,
        reward_chart,
        run_picker,
        totals,
        whole,
    )


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
def _(LEDGER, RUNS, totals):
    TOTALS = totals(LEDGER, RUNS)
    return (TOTALS,)


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
def _(RUNS, benchmark_rows):
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
def _(RUNS, mo, run_picker):
    mo.Html(f'<div class="story section"><h2>The runs</h2>{run_picker(RUNS)}</div>')
    return

@app.cell
def _(RUNS, reward_chart):
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
def _(RUNS, acceptance_moves, dumbbell):
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
def _(RUNS, loop_bars):
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

