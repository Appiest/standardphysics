# Lane C — rules, checks, the router and the gate

What counts as a problem, and what the loop does next.

```bash
python -m pip install -e packages/agents
python -m pytest packages/agents -q
```

## The shape of it

```
rules/            the thresholds, and the ledger that decides which ones run
checks/           one module per section, each answering one geometric question
copy.py           every sentence the owner reads
numbers.py        the display boundary, where inches become words
findings.py       a measurement becomes a problem, a pass or a request
assess.py         one pass over a shop
ask/              any question about the room, answered from the measured model
router/           the closed action set, validated before it authorizes anything
fix/              rearrangements that keep the owner's furniture
loop.py           the action choosing the branch, and the gate letting it through
evaluation/       the labelled dataset, the scorers and the acceptance gate
models.py         every model call, through OpenRouter
strict_schema.py  the shape a model is held to, generated from the model
```

## The ask box

Any question about the shop, not a menu of them.

```bash
python -m standardphysics_agents.cli ask how many chairs do I have
python -m standardphysics_agents.cli ask how are my tables laid out
python -m standardphysics_agents.cli ask do I have space for a 97 inch couch
```

```
You have six chairs.
Your four tables sit in two rows of two, 15 feet 1 inch between the rows.
Give us one more number about the couch. Measure how deep it is, front to back.
```

Eight kinds of question, each with one executor: how many of something there
is, how big it is, how far apart two things are, where something is, what shape
a set of furniture makes, whether something would fit, moving furniture, and
whether something meets the standards.

**The model picks the question and the code supplies the facts.** Working out
that "how would you describe my table arrangement" is a question about shape is
judgment, and a model is good at it. Knowing the tables sit in two rows 15 feet
apart is measurement, and a model is not. A model that returns a height along
with the question has that height ignored, and there is a test that says so.

Every answer carries a locus, so asking about the tables sends the camera to
the tables. That is the difference between a room you can talk to and a list of
measurements.

**The endpoint enforces the shape, so a malformed answer never reaches an
executor.** The call asks for strict structured output against `Query`, which
`strict_schema.py` generates from the model: every property named in
`required`, `additionalProperties: false`, and the keywords strict mode refuses
left out. An optional field says so by being nullable, the way Pydantic already
writes `X | None`. Handing over the raw Pydantic schema is a 400 on every call,
and the ask box answers "we did not follow that one" when a call is refused, so
the failure reads as a question nobody understood. `parse_query` still rejects
what arrives: a kind outside the set, a measurement longer than a shop, a
question that names nothing.

## The router

**TypeSafe picks the next action and the code names what it acts on.** The
request is one Choice question over the five actions, with the measured state
as its content, so the answer is `FIX`, `RESCAN_AREA`, `ASK_OWNER`, `ESCALATE`
or `DONE` by construction. System One answers typed questions and does not
generate arrays of UUIDs, so the targets are assembled from the findings after
the choice, then validated by the same `parse_decision` every router goes
through. `_authorize` is the last gate: a fix only where furniture can clear
the problem and only while attempts remain, a rescan only on geometry somebody
wants another look at, an escalation only on a problem.

The answer also carries a confidence and a probability for each action it did
not pick, which a `Decision` has no room for. `router.typesafe.systemone` is
traced on its own for that reason, so a trace says how close the call was when
a loop does something surprising. In practice the calls land between 0.5 and
0.9, often with two actions within 0.05 of each other.

An action the shop has no work for, a body that is not the shape the service
documents, and a service that is down all come back as `Rejected`, which no
handler is registered for. `TypeSafeCallBudget` caps paid calls for a campaign.
Without `TYPESAFE_BASE_URL` the loop runs `router/local_policy.py` and labels
every decision `local_policy`, so a trace always says which one answered.

## Turning a check on

**No check runs until a person has read its section.** `rules review` prints the
sentence from the standard and asks for the number back. Typing it is the
confirmation: it cannot be given by somebody who did not read the section.

```bash
python -m standardphysics_agents.cli rules review --by "your name"
python -m standardphysics_agents.cli rules list
python -m standardphysics_agents.cli rules second-check route_clear_width --by "someone else"
```

A ledger entry binds the rule id, the section, the threshold and the unit
together. Editing a threshold invalidates its verification and the check
switches itself off, so there is no edit an agent can make that both moves a
number and keeps the check running.

The ledger lives at `rules/data/verification.json`, or wherever
`STANDARDPHYSICS_VERIFICATION_LEDGER` points.

## Running it

```bash
python -m standardphysics_agents.cli check       # the checks, on the fixture shop
python -m standardphysics_agents.cli loop        # the whole loop
python -m standardphysics_agents.cli evaluate    # the dataset and the scorers
```

## From another lane

```python
from standardphysics_agents import RULEPACK_VERSION, findings_for

findings = findings_for(graph, scenario)   # problems, then requests, then passes
```

For everything else, `assess` returns the whole pass:

```python
from standardphysics_agents import assess
from standardphysics_pipeline import PipelineMeasurements

result = assess(graph, scenario, PipelineMeasurements())
for finding in result.problems:
    print(finding.title, "-", finding.detail, "-", finding.fix)
```

`result.unevaluated` lists rules held but not answered, with what each is
waiting on: a person to read a section, a measurement a provider cannot take
yet. It goes to the team and never to the owner, because a rule nobody could
answer must not look like a clean pass.

## Four rules the code enforces on itself

**Thresholds live in the rule pack and nowhere else.** A check reads the number
off its rule. It never compares against a literal, and it never takes a
provider's word for whether something passed. Lane B's turn measurement carries
four numbers and no thresholds for the same reason.

**A number nobody is sure of is not evidence.** A check resting on geometry
marked `needs_another_look` becomes a request to point the phone again, whatever
the measurement said.

**A fix keeps the furniture.** Movable nodes slide on the floor and turn about
Z. Nothing resizes, nothing leaves the floor, nothing lands in a wall or a
door's swing, and the inventory comes out equal. When the search runs out the
answer is one specific thing the owner could allow, tested first so the offer is
real.

**Nothing is accepted without measuring it again.** A rearrangement goes through
`evaluation/gate.py`, which wants four things at once: no rule stopped being
answerable, no check stopped reporting, no new problem appeared, and something
measurable actually improved. Counting problems alone would reward dropping a
check.

## Tracing

`weave.init()` happens once, through `init_tracing()`. Every check and every
agent call carries `@traced`, which costs one attribute read when Weave is not
configured, because the tests run in CI and CI has no keys.

Set `WANDB_PROJECT` and `WANDB_ENTITY` in the repo-root `.env` and the whole
loop reads as one trace tree. The API server calls `init_tracing()` at startup
and logs the project URL, so an upload through the web app traces itself. A key
Weave rejects logs a warning and leaves the server running untraced.

A run of the loop also shows up in Weave's Agents tab as `standardphysics-loop`:
one conversation per run, one turn per pass, a tool span for `assess`,
`propose_fix`, `gate` and each other branch, and a chat span for each TypeSafe
and Astra call. Spans hold counts and verdicts, never the scene graph or a key.
Weave's automatic model-client patching is off, so no call is recorded twice.

`evaluate` writes per-case results to `runs/evaluation.json` either way, so a
run can always be looked at again. With Weave configured it also logs one eval
per run, labelled with the router that answered:

    standardphysics-agents evaluate --router local
    standardphysics-agents evaluate --router typesafe --version typesafe-last-search

`loop` ends by printing `trajectory_ok` for the run: whether every pass after the
kept rearrangement handed something new to a person, or repeated work.

## The outer loop

    standardphysics-agents evolve --generations 3

The review loop is the inner loop: measure, TypeSafe picks an action, the gate
keeps a layout only if it measures better. `evolve` is the loop around it,
built the way self-evolving agents are described (Fang et al., 2025): an agent,
an environment and an optimizer closing a feedback loop.

1. Score TypeSafe on the labelled cases. Every case goes into
   `runs/experience.jsonl`, the memory.
2. Take the cases where it chose wrong or repeated work. Astra reads them and
   the most similar past failures, and writes one lesson about which action to
   choose and when. With no Astra, a labelled local rule writes one.
3. Score a playbook holding that lesson on the failing cases plus four it
   already passes. Keep the lesson only if `router_action_match` and
   `trajectory_ok` rise and nothing falls. Each trial is its own eval in Weave.
4. Repeat with the kept playbook until nothing fails or nothing new is left to
   try. `runs/playbook.json` is what the API's loop then reads.

A lesson can only change which action TypeSafe picks. One that mentions
thresholds, inches or unlocking furniture is refused before it is tried, and
the measurements, the rule pack and `accepts()` stay the fixed ground truth it
is scored against.

## Evaluations in Weave

    standardphysics-agents weave-eval

The same 39 cases and the same eight scorers, run through `weave.Evaluation` so
the Evals tab holds them: a mean per scorer, the per-case table behind each
mean, and a side by side of the configurations in
`evaluation/weave_eval.py:DEFAULT_SETUPS`. Each configuration changes one part
of the system without touching what a correct answer is, so the difference
between two columns says what that part is worth.

Swapping Lane B's pipeline for the fixtures' simplified stand-in is the sharpest
of them. On the shipped shop the stand-in agrees; across the 39 cases, which
move the geometry, it merges axis-aligned boxes along a straight leg and gets a
mean error of 8 inches, `finding_precision` of 0.38 against the pipeline's 1.00,
and a different router action on 13% of cases. Nearly all of the score rests on
measuring the room.

Scoring stays in `evaluation/scorers.py`. A scorer reads a whole `CaseOutcome`,
which is more than a dataset row can hold, so each Weave scorer reports the
number that module computed instead of recomputing it from the row.

`--cases N` scores the first N for a quick look. `--preview-unverified` scores
as if a person had verified every rule, which is development only and the same
escape hatch as the server's `SP_PREVIEW_UNVERIFIED_RULES`: without it, a rule
nobody has read is not scored.
