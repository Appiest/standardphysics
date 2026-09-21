# Can a model write a predicate nobody gave it?

The experiment behind the "What the experiment showed" section of
`docs/ARCHITECTURE.md`. Kept because the claim it tests is the one the whole
architecture rests on, and because it should be re-run whenever the grammar or
the model changes.

It loads the real `datasets/phone/test1` scan, strips every label, and asks a
model to write predicates for things it was given no word for. Labels are read
back only to score the answer.

```bash
set -a && . ./.env && set +a
.venv/bin/python scripts/emergence_spike/experiment.py anthropic/claude-sonnet-5
```

About five cents a run on a frontier model. The models that failed are listed
in the architecture doc; re-running them is how you find out whether a cheaper
one has caught up.
