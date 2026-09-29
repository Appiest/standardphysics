"""The single HTML page the rating tool serves."""

PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Rate layouts</title>
<style>
  :root {
    --surface: #f6f4ef;
    --panel: #ffffff;
    --ink: #201d18;
    --ink-muted: #6b6459;
    --line: #ded8cb;
    --floor-fill: #efe9dc;
    --wall: #3c362c;
    --portal-gap: #efe9dc;
    --window-mark: #7fa6bd;
    --door-line: #a08a5f;
    --object-fill: #e4d9c2;
    --object-fill-moved: #cbb98e;
    --object-stroke: #b7a67d;
    --ghost-fill: #d8d2c4;
    --ghost-stroke: #8a8272;
    --accent: #c9622f;
    --control-bg: #eee8db;
    --control-bg-hover: #e4dcc9;
    --control-selected: #201d18;
  }
  * { box-sizing: border-box; }
  html, body { overflow-x: hidden; }
  body {
    margin: 0;
    background: var(--surface);
    color: var(--ink);
    font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
    display: flex;
    flex-direction: column;
    align-items: center;
    min-height: 100vh;
    width: 100%;
    padding: 28px 20px 40px;
  }
  .wide-only { display: inline; }
  .narrow-only { display: none; }
  .top {
    width: 100%;
    max-width: 1180px;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px 16px;
    margin-bottom: 18px;
  }
  button.back {
    background: transparent;
    border: none;
    color: var(--ink-muted);
    font-size: 0.9rem;
    cursor: pointer;
    padding: 10px 4px;
    order: -1;
  }
  button.back:hover { color: var(--ink); }
  button.back:disabled { opacity: 0.35; cursor: default; }
  h1 { font-size: 1.15rem; font-weight: 600; margin: 0; flex: 1 1 200px; min-width: 0; }
  .progress { color: var(--ink-muted); font-size: 0.95rem; white-space: nowrap; }
  .panels {
    display: flex;
    gap: 24px;
    max-width: 1180px;
    width: 100%;
    justify-content: center;
  }
  .panel {
    background: var(--panel);
    border-radius: 16px;
    box-shadow: 0 1px 2px rgba(32,29,24,0.08), 0 8px 24px rgba(32,29,24,0.06);
    padding: 16px;
    flex: 1;
    min-width: 0;
    display: flex;
    justify-content: center;
    cursor: pointer;
    transition: box-shadow 150ms ease-out, transform 150ms ease-out;
  }
  .panel:hover { box-shadow: 0 1px 2px rgba(32,29,24,0.1), 0 10px 28px rgba(32,29,24,0.1); }
  .panel:active { transform: scale(0.99); }
  .panel svg { max-width: 100%; height: auto; }
  .floor { fill: var(--floor-fill); stroke: var(--line); stroke-width: 1.5; }
  .wall { stroke: var(--wall); stroke-linecap: square; }
  .portal-gap { stroke: var(--portal-gap); stroke-linecap: butt; }
  .window-mark { stroke: var(--window-mark); stroke-linecap: butt; }
  .door-leaf { stroke: var(--door-line); stroke-width: 1.5; }
  .door-arc { fill: none; stroke: var(--door-line); stroke-width: 1; stroke-dasharray: 3 3; }
  .object { fill: var(--object-fill); stroke: var(--object-stroke); stroke-width: 1; }
  .object.moved { fill: var(--object-fill-moved); }
  .object-label {
    font-size: 13px;
    fill: var(--ink);
    text-anchor: middle;
    dominant-baseline: middle;
    pointer-events: none;
  }
  .ghost { fill: var(--ghost-fill); fill-opacity: 0.5; stroke: var(--ghost-stroke); stroke-width: 1.5; stroke-dasharray: 5 4; }
  .ghost-trail { stroke: var(--ghost-stroke); stroke-width: 2; opacity: 0.85; }
  .front-marker { stroke: var(--accent); stroke-width: 4; stroke-linecap: round; }
  .front-marker.ghost { stroke: var(--ghost-stroke); stroke-width: 3; }
  .controls {
    display: flex;
    gap: 12px;
    justify-content: center;
    width: 100%;
    max-width: 1180px;
    margin-top: 22px;
  }
  button.choice {
    background: var(--control-bg);
    border: none;
    border-radius: 10px;
    padding: 12px 22px;
    font-size: 0.95rem;
    line-height: 1.2;
    color: var(--ink);
    cursor: pointer;
    transition: background 150ms ease-out, box-shadow 150ms ease-out;
    box-shadow: 0 1px 2px rgba(32,29,24,0.06);
    flex: 1 1 0;
  }
  button.choice:hover { background: var(--control-bg-hover); }
  button.choice:active { transform: scale(0.96); }
  .hint { color: var(--ink-muted); font-size: 0.82rem; margin-top: 10px; text-align: center; }
  .summary {
    max-width: 480px;
    text-align: center;
    margin-top: 80px;
  }
  .summary h2 { font-size: 1.4rem; margin-bottom: 10px; }
  .summary p { color: var(--ink-muted); line-height: 1.5; }

  @media (max-width: 900px) {
    body { padding: 20px 14px 32px; }
    .wide-only { display: none; }
    .narrow-only { display: inline; }
    .panels { flex-direction: column; gap: 16px; }
    .controls { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    button.choice { padding: 12px 8px; font-size: 0.88rem; }
  }
</style>
</head>
<body>
<div id="app"></div>
<script>
const app = document.getElementById("app");
let state = { index: 0, total: 30, done: false };
let currentPair = null;
let pending = null;

async function fetchJSON(url, opts) {
  const res = await fetch(url, opts);
  return res.json();
}

async function loadState() {
  state = await fetchJSON("/api/state");
  if (state.done) { renderSummary(); return; }
  await loadPair(state.index);
}

async function loadPair(index) {
  currentPair = await fetchJSON("/api/pair/" + index);
  pending = null;
  render();
}

function render() {
  if (!currentPair) return;
  app.innerHTML = `
    <div class="top">
      <button class="back" id="back" ${state.index === 0 ? "disabled" : ""}>Back</button>
      <h1>Which rearrangement looks better?</h1>
      <div class="progress">${state.index + 1} of ${state.total}</div>
    </div>
    <div class="panels">
      <div class="panel" data-side="left">${currentPair.left_svg}</div>
      <div class="panel" data-side="right">${currentPair.right_svg}</div>
    </div>
    <div class="controls">
      <button class="choice" data-pick="left">
        <span class="wide-only">Left looks better</span>
        <span class="narrow-only">Top looks better</span>
      </button>
      <button class="choice" data-pick="right">
        <span class="wide-only">Right looks better</span>
        <span class="narrow-only">Bottom looks better</span>
      </button>
      <button class="choice" data-pick="tie">Can't tell</button>
    </div>
    <div class="hint">
      <span class="wide-only">Left/right arrows or 1/2 pick a side. Down arrow or 3 is can't tell.</span>
      <span class="narrow-only">Up/down arrows or 1/2 pick a side. 3 is can't tell.</span>
    </div>
  `;
  app.querySelector('[data-side="left"]').addEventListener("click", () => choose("left"));
  app.querySelector('[data-side="right"]').addEventListener("click", () => choose("right"));
  app.querySelectorAll("button.choice").forEach((button) => {
    button.addEventListener("click", () => choose(button.dataset.pick));
  });
  const back = document.getElementById("back");
  if (!back.disabled) back.addEventListener("click", goBack);
}

async function choose(picked) {
  if (pending) return;
  pending = picked;
  await fetchJSON("/api/answer", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pair_id: currentPair.pair_id, picked, left_was: currentPair.left_was }),
  });
  state.index += 1;
  if (state.index >= state.total) { renderSummary(); return; }
  await loadPair(state.index);
}

async function goBack() {
  if (state.index === 0) return;
  state.index -= 1;
  await loadPair(state.index);
}

function renderSummary() {
  state.done = true;
  app.innerHTML = `
    <div class="summary">
      <h2>All ${state.total} ratings saved</h2>
      <p>Your answers were written as they were made. You can close this tab.</p>
    </div>
  `;
}

const stackedQuery = window.matchMedia("(max-width: 900px)");

window.addEventListener("keydown", (event) => {
  if (state.done) return;
  const map = stackedQuery.matches
    ? { ArrowUp: "left", "1": "left", ArrowDown: "right", "2": "right", "3": "tie" }
    : { ArrowLeft: "left", "1": "left", ArrowRight: "right", "2": "right", ArrowDown: "tie", "3": "tie" };
  if (event.key in map) { choose(map[event.key]); return; }
  if (event.key === "Backspace") goBack();
});

loadState();
</script>
</body>
</html>
"""
