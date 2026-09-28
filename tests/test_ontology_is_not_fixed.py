"""A ratchet against deciding in advance what kinds of thing exist.

The app has to work somewhere nobody has been, where the things have no names
we know and stand in relations we have no words for. Every `Literal` of kinds
or relations, and every branch on one, is a place that assumption is baked in.

A complexity ceiling cannot catch this. The coupling is not one branchy
function; it is one comparison in each of twenty-five files. So this counts
them instead, and only lets the count fall. Lowering a baseline here is the
work; raising one is the thing this test exists to stop.

The counts are printed so that progress is visible to anything reading the run
rather than only to whoever opened the file.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ("packages/contracts", "packages/pipeline", "packages/agents", "services/api")

BRANCHES_ON_KIND = re.compile(r'\.kind\s*(?:==|!=)\s*["\']|\.kind\s+(?:not\s+)?in\s*[\(\[{]')
ONTOLOGY_LITERAL = re.compile(
    r'^(?:NodeKind|Relation|QueryKind|Dimension|LabelSource)\s*(?::\s*\w+\s*)?=\s*Literal\[', re.M
)

BRANCHES_BASELINE = 45
"""Places that ask what kind of thing something is. Target: nothing above the
interpretation layer asks, because a name is for showing a person.

Raising this baseline is only ever an audit, never a shortcut. The last audit
(2026-09-21, lane R, commit 209b1c7+) counted 34:

- 7 annotation/artefact kinds: what a measurement or stored evidence object
  is, not what a thing in the room is (fix/pinch.py 2, evaluation/captures.py
  2, evaluation/scan_space.py 1, api/app.py 1, api/textures.py 2).
- 13 structural predicates: door/portal or wall roles that could each become
  a named predicate like `lies_flat` (checks/roles.py, checks/walls.py,
  workflows.py 2, pipeline/astra.py 3, pipeline/ingest.py,
  discovery/surface_attach.py, discovery/semantic_corrections.py 2,
  agents/scenario_suggestion.py). This is the backlog for lowering the count.
- 5 target-class labels the pilot scope must name (api/labels.py,
  api/scope_manifest.py 4).
- 9 presentation kinds in api/architecture_export.py, which draws walls,
  portals and mounts differently.
Previously 15; the growth came from the A/G/K/E integrations above, not from
this file's owner. Any count above 34 must fail and be audited here.

Audit of 2026-09-24 (merging finetune/multiroom-data), 24 to 27:

- 2 in training/prompt.py, which lists walls and doors by kind. That JSON is
  the exact input the fine-tuned rearrangement model was trained on, and the
  web's suggestion endpoint must send it unchanged, so these two stay until
  the model is retrained on a prompt built from predicates.
- 1 in training/quality.py, the wall term of Q, which is logged beside the
  reward and never paid. It can become `stands_upright` with the prompt.

Audit 2026-09-26, lane D: 24 rose to 30 with the owner journey. All six are in
api/owner_requests.py and branch on how the owner answers a request (a yes or
no, a number, a photo, or another walk of the shop). That is the form of an
answer, like the artefact kinds above, not what a thing in the room is.

Audit 2026-09-27, merging feat/model-sees-the-room: 30 rose to 39.
- 5 branch on the form of an owner's wish, not on a thing in the room:
  contracts/wishes.py (stays_near needs an anchor), training/owner.py 3
  (stays_put, with_table, against_wall), training/explain.py 1 (clear_view).
- 4 are wall, door and floor roles in the rearranger's training code:
  training/prompt.py 2 (the walls and doors the model reads),
  training/quality.py 1 and training/explain.py 1. They join the structural
  backlog above. prompt.py is the text the served fine-tune was trained on,
  so replacing those two with predicates means retraining or re-checking it.

Audit 2026-09-27, merging feat/ada-precedent-corpus into unified: 39 fell to 36.
That branch had already moved training/prompt.py (walls and doors) and
training/quality.py onto checks.walls.standing_walls and checks.roles.doors,
which select exactly the nodes the kind tests did on the 2,075 synthetic
training graphs (commit 1ca5be72), so the served fine-tune reads the same room.

Audit 2026-09-27, merging fine-tuning into unified: 36 rose to 44. The
fine-tuning branch had already reached 33 against its own baseline of 27
without an audit; these eight are the ones it adds here:
- training/snapped_prompt.py 3: the Fireworks model's prompt, kept byte for
  byte as trained (walls and doors by kind, and facing_away's object test).
  It retires only by retraining that model on a prompt built from predicates.
- snap/solver.py 3: the solver treats walls and objects as obstacles, a
  room-bounding node that is not a wall as the shell, and snaps only seats
  that are objects.
- training/usefulness.py 2: a seat or a wall-backed piece is only counted
  when it is an object, not a door or a wall that happens to match the seat
  or shelf test.
The solver and usefulness five ask whether a node is a piece of furniture rather than structure, the
same question as the structural backlog above, and one `is_furniture`
predicate would retire them together.

Audit 2026-09-28, merging fix/sign-in-api-port into unified: 44 rose to 45.
api/furniture.py 1 picks the scanned objects furniture refinement may
replace with a model, the same furniture-not-structure question as above.
"""

LITERALS_BASELINE = 0
"""Closed sets naming what can exist. Target: zero."""


def _source_files():
    for area in SOURCE:
        for path in (ROOT / area).rglob("*.py"):
            if not any(part in ("tests", "__pycache__") for part in path.parts) and not path.name.startswith("test_"):
                yield path


def _count(pattern, per_file=False):
    total, worst = 0, []
    for path in _source_files():
        found = len(pattern.findall(path.read_text(encoding="utf-8")))
        if found:
            total += found
            worst.append((found, path.relative_to(ROOT)))
    worst.sort(reverse=True)
    return (total, worst) if per_file else total


def test_nothing_new_branches_on_what_kind_of_thing_it_is():
    total, worst = _count(BRANCHES_ON_KIND, per_file=True)
    print(f"\nbranches on kind: {total} (baseline {BRANCHES_BASELINE}, target 0)")
    for found, path in worst[:5]:
        print(f"    {found:3}  {path}")
    assert total <= BRANCHES_BASELINE, (
        f"{total} places ask what kind of thing something is, up from {BRANCHES_BASELINE}. "
        "Re-run the predicate that justifies the relation instead of trusting the name."
    )


def test_no_new_closed_set_names_what_can_exist():
    total = _count(ONTOLOGY_LITERAL)
    print(f"closed ontology sets: {total} (baseline {LITERALS_BASELINE}, target 0)")
    assert total <= LITERALS_BASELINE, (
        f"{total} Literals name what kinds of thing can exist, up from {LITERALS_BASELINE}. "
        "A kind or a relation is a string a model coined, with the predicate that justifies it."
    )


def test_the_baselines_are_honest():
    """If a baseline is above the real count, lower it in the same change."""
    branches, _ = _count(BRANCHES_ON_KIND, per_file=True)
    literals = _count(ONTOLOGY_LITERAL)
    assert branches == BRANCHES_BASELINE, f"branches fell to {branches}; set BRANCHES_BASELINE to that"
    assert literals == LITERALS_BASELINE, f"literals fell to {literals}; set LITERALS_BASELINE to that"
