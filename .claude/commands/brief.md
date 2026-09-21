---
description: The standing brief for Standard Physics. Read before planning any work.
---

Read `docs/MISSION.md` and `docs/ARCHITECTURE.md` before you plan anything.

This is the brief every agent working on this repository builds against. It is
not a completion condition: `/goal` is the built-in that carries one of those.

The target is not a demo and not a prize. Standard Physics runs in production,
ten people scan their own spaces with it, and every one of them says it is
immaculate and does everything it is supposed to do.

## The one idea

Scan an alien planet with it. Things nobody has a name for, standing in
relations nobody has a word for, under physics we did not assume. Everything in
this repository has to survive that.

So there are three layers, and only the bottom one is closed:

1. **Substrate.** Measured regions and the operators over them: hulls, volumes,
   overlap fractions, signed gaps along a measured direction, free space,
   adjacency, which frames saw what, glyphs on a surface. Mathematics, so it is
   the same everywhere. Nothing here says wall, floor or up.
2. **Interpretation.** A model coins entities and relations from the substrate
   and stores them as data. A name is a free string. A relation carries the
   substrate predicate that justifies it, so anything needing the truth re-runs
   the predicate rather than trusting the word.
3. **Composition.** A question arrives and a model writes an expression over
   the substrate to answer it, authoring predicates nobody anticipated. Asked
   which things are precariously balanced, it builds that test out of
   centroids, footprints and contact areas rather than needing someone to have
   written `precariously_balanced` first.

## What this rules out

- No `Literal` of kinds, relations, question types, intents or roles. If you
  are about to write one, you are rebuilding the thing this brief exists to
  remove.
- No code above layer 2 branching on a name. Names are for showing a person.
- No primitive per example. The three questions in `MISSION.md` are examples;
  a primitive built for each is the same failure one level down.

## What never bends

- **The model supplies structure, the engine supplies values.** A model says
  which regions to compare and how. It never says what the comparison returned,
  and no number in an answer comes from model text.
- **Nothing renders unverified.** Every entity referenced exists in this scan,
  every number traces to an evaluated expression, the view has content. A
  failure re-plans once, then says what it could not establish. An empty screen
  is a bug and a confident wrong answer is worse.

## Providers

Fireworks with open weights for volume work: per-region labelling, glyph
reading, embeddings. OpenRouter for frontier reasoning: coining entities and
relations, writing expressions, composing views. A call goes to OpenRouter only
when open weights cannot do it, and the reason lives in `providers/policy.py`.

## Proving work is done

`/goal` judges a condition from what lands in the conversation, not by reading
files itself. A claim nobody demonstrated did not happen.

- Run the checks and let the output land in the transcript: `.venv/bin/python
  -m pytest -q`, `.venv/bin/python -m ruff check .`, and in `apps/web`, `npm
  run lint && npm run typecheck && npm run test`.
- A claim about behaviour needs a test that failed before and passes after.
  Show both runs.
- Never weaken, skip, xfail or delete a test to make a check pass.
- A structural check is never the proof on its own. Deleting a file and showing
  an empty grep says nothing about whether the app answers anything. The proof
  is the held-out suite in `MISSION.md`, scored against real scenes.

$ARGUMENTS
