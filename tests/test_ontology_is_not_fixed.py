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

BRANCHES_BASELINE = 91
"""Places that ask what kind of thing something is. Target: nothing above the
interpretation layer asks, because a name is for showing a person."""

LITERALS_BASELINE = 5
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
