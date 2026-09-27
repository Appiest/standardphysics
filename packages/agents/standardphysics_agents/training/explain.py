"""What the owner reads after the rearranger changes their shop.

The rearranger picks a room from measured facts, and the owner never sees the
geometry it reasoned over, only a plain paragraph. That paragraph has to come
from the same facts the rearranger checked, not from a model's memory of what
it meant to do: what moved and by how far (`quality.moved_ids`, `edits.
yaw_degrees`), which accessibility problem that move cleared or eased, with
its ADA section and the before and after measurement (`TrainingChecker`), and
which of the owner's own wishes survived the change (`wishes.kept`). The model
that picked the option may add one sentence of its own reasoning, carried
through unchanged and always labelled as the model's reason rather than a
measured fact.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass

from standardphysics_contracts import Finding, SceneGraph, SceneNode, to_inches

from ..numbers import _whole_or_tenth
from ..rules import RuleSpec
from .checker import TrainingChecker
from .edits import yaw_degrees
from .quality import _xy, moved_ids
from .wishes import Wish
from .wishes import kept as wish_kept

ID_TAG = re.compile(r" \[[0-9a-fA-F]{4}\]")
"""The " [abcd]" tag `wishes._name` adds to tell twins apart. Owners never see ids."""

COMPARISON_WORDS = {"at_least": "at least", "at_most": "at most"}

TELL_US = "Tell us if any of these matter to you and we will find another way."


@dataclass(frozen=True)
class Explanation:
    moves: list[str]
    fixed: list[str]
    kept: list[str]
    bent: list[str]
    why: str

    def as_dict(self) -> dict:
        return asdict(self)

    def text(self) -> str:
        paragraphs = [paragraph for paragraph in (
            _moves_paragraph(self.moves),
            _fixed_paragraph(self.fixed),
            _wishes_paragraph(self.kept, self.bent),
            _why_paragraph(self.why),
        ) if paragraph]
        return "\n\n".join(paragraphs)


def _moves_paragraph(moves: list[str]) -> str:
    return " ".join(["Here is what changed.", *moves]) if moves else ""


def _fixed_paragraph(fixed: list[str]) -> str:
    return " ".join(fixed)


def _wishes_paragraph(kept: list[str], bent: list[str]) -> str:
    sentences = []
    if kept:
        sentences.append(f"We kept: {', '.join(kept)}.")
    if bent:
        sentences.append(f"We had to bend: {', '.join(bent)}.")
        sentences.append(TELL_US)
    return " ".join(sentences)


def _why_paragraph(why: str) -> str:
    return f"Why this option: {why}" if why else ""


def _piece_name(node: SceneNode) -> str:
    return node.label.lower()


def _twinned(node: SceneNode, before: SceneGraph) -> bool:
    """Whether another piece in the room shares this one's label."""
    return sum(1 for other in before.nodes if other.label.casefold() == node.label.casefold()) > 1


def _nearest_other(node: SceneNode, before: SceneGraph) -> SceneNode | None:
    candidates = [other for other in before.nodes if other.id != node.id and other.kind not in ("wall", "floor")]
    return min(candidates, key=lambda other: math.dist(_xy(node), _xy(other)), default=None)


def _anchor_phrase(node: SceneNode, before: SceneGraph) -> str | None:
    """"beside the table", only when the room has more than one piece with this label."""
    if not _twinned(node, before):
        return None
    anchor = _nearest_other(node, before)
    return None if anchor is None else f"the {_piece_name(anchor)}"


def _turn_degrees(before: SceneNode, after: SceneNode) -> float:
    """The turn from `before` to `after`, normalised to -180..180 degrees."""
    turn = yaw_degrees(after) - yaw_degrees(before)
    return (turn + 180.0) % 360.0 - 180.0


def _move_sentence(before_node: SceneNode, after_node: SceneNode, before: SceneGraph) -> str:
    dx = after_node.transform.position.x - before_node.transform.position.x
    dy = after_node.transform.position.y - before_node.transform.position.y
    inches_moved = round(to_inches(math.hypot(dx, dy)))
    turn = round(abs(_turn_degrees(before_node, after_node)))
    subject = f"the {_piece_name(before_node)}"
    anchor = _anchor_phrase(before_node, before)
    if anchor:
        subject = f"{subject} beside {anchor}"
    if inches_moved == 0 and turn >= 1:
        return f"Turn {subject} {turn} degrees."
    if turn >= 1:
        return f"Move {subject} {inches_moved} in and turn it {turn} degrees."
    return f"Move {subject} {inches_moved} in."


def _move_sentences(before: SceneGraph, after: SceneGraph) -> list[str]:
    before_by_id = {node.id: node for node in before.nodes}
    after_by_id = {node.id: node for node in after.nodes}
    ordered = sorted(moved_ids(before, after), key=lambda node_id: (before_by_id[node_id].label, node_id))
    return [_move_sentence(before_by_id[node_id], after_by_id[node_id], before) for node_id in ordered]


def _measured_phrase(value: float | None) -> str:
    return "no clear space" if value is None else f"{_whole_or_tenth(value)} in"


def _cleared_sentence(finding: Finding, rule: RuleSpec) -> str:
    return (f"Fixed: {finding.title} (ADA {rule.citation.section}). "
            f"It measured {_measured_phrase(finding.measured_inches)}; the standard asks for "
            f"{COMPARISON_WORDS[rule.comparison]} {_whole_or_tenth(rule.threshold)} in.")


def _improved_sentence(finding: Finding, rule: RuleSpec, after_inches: float | None) -> str:
    return (f"Improved: {finding.title} (ADA {rule.citation.section}), from "
            f"{_measured_phrase(finding.measured_inches)} to {_measured_phrase(after_inches)}; "
            f"the standard asks for {COMPARISON_WORDS[rule.comparison]} {_whole_or_tenth(rule.threshold)} in.")


def _fixed_sentence(finding: Finding, rule: RuleSpec, after_problems: dict) -> str | None:
    still_there = after_problems.get(finding.id)
    if still_there is None:
        return _cleared_sentence(finding, rule)
    if _measured_phrase(still_there.measured_inches) == _measured_phrase(finding.measured_inches):
        return None
    return _improved_sentence(finding, rule, still_there.measured_inches)


def _fixed_sentences(checker: TrainingChecker, before: SceneGraph, after: SceneGraph) -> list[str]:
    fixable = checker.fixable_problems(checker.assess(before))
    after_problems = {finding.id: finding for finding in checker.assess(after).problems}
    sentences = (_fixed_sentence(finding, checker.rules.by_id(finding.check_id), after_problems)
                 for finding in fixable)
    return [sentence for sentence in sentences if sentence is not None]


def _clean(text: str) -> str:
    """A wish's text, with its " [abcd]" id tag removed: owners never see ids."""
    return ID_TAG.sub("", text)


def _touched(wish: Wish, moved: set) -> bool:
    """Whether the change could have affected this wish: it moved one of its pieces, or, for the view, anything."""
    return bool(moved) if wish.kind == "clear_view" else bool(set(wish.subjects) & moved)


def _kept_and_bent(wishes: list[Wish], before: SceneGraph, after: SceneGraph,
                    checker: TrainingChecker) -> tuple[list[str], list[str]]:
    """The wishes about pieces that moved, split by whether they survived; repeats are said once."""
    moved = moved_ids(before, after)
    kept_texts: list[str] = []
    bent_texts: list[str] = []
    for wish in (wish for wish in wishes if _touched(wish, moved)):
        bucket = kept_texts if wish_kept(wish, before, after, checker.measure) else bent_texts
        if _clean(wish.text) not in bucket:
            bucket.append(_clean(wish.text))
    return kept_texts, bent_texts


def explain_change(before: SceneGraph, after: SceneGraph, checker: TrainingChecker,
                    wishes: list[Wish], why: str = "") -> Explanation:
    """What changed, in the owner's own words, built only from what was measured."""
    kept_texts, bent_texts = _kept_and_bent(wishes, before, after, checker)
    return Explanation(
        moves=_move_sentences(before, after),
        fixed=_fixed_sentences(checker, before, after),
        kept=kept_texts,
        bent=bent_texts,
        why=why.strip(),
    )
