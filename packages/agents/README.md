# Lane C — rules, checks, the router and the gate

What counts as a problem, and what the loop does next.

```bash
python -m pip install -e packages/agents
python -m pytest packages/agents -q
```

## The shape of it

```
rules/          the thresholds, and the ledger that decides which ones run
checks/         one module per section, each answering one geometric question
copy.py         every sentence the owner reads
numbers.py      the display boundary, where inches become words
findings.py     a measurement becomes a problem, a pass or a request
assess.py       one pass over a shop
ask/            any question about the room, answered from the measured model
router/         the closed action set, validated before it authorizes anything
fix/            rearrangements that keep the owner's furniture
loop.py         the action choosing the branch, and the gate letting it through
evaluation/     the labelled dataset, the scorers and the acceptance gate
models.py       every model call, through OpenRouter
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

`evaluate` writes per-case results to `runs/evaluation.json` either way, so a
run can always be looked at again.
