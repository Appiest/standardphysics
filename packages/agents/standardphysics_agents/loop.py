"""One trip round the loop, with the router's answer choosing the branch.

The point of this file is that the action is not advice. `FIX` runs the search
and can change the layout. `RESCAN_AREA` asks for more scan. `ASK_OWNER` puts a
question in front of a person. `ESCALATE` queues something for a professional.
`DONE` ends the report. There is one handler per action and no default, so an
answer that does not validate reaches no handler and the layout, the questions
and the report are exactly as they were.

A `FIX` does not get to change anything on its own either. The rearrangement it
produces is measured again and put through the gate, and the layout only moves
if the gate accepts it.

Each run is one conversation in Weave's Agents tab and each pass is one turn,
with a tool span for the assessment, the search, the gate and every other
branch. The spans hold counts and verdicts, never the scene graph.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterator
from uuid import UUID

from standardphysics_contracts import (
    Assessment,
    Decision,
    MeasurementProvider,
    Proposal,
    Scenario,
    SceneGraph,
)
from standardphysics_contracts.loop import RouterAction
from standardphysics_contracts.rules import Tier

from .assess import Pass, assess
from .copy import REPORT_READY, escalation_note
from .evaluation.gate import GateResult, accepts
from .fix.search import CandidateRejection, FixOutcome, propose_fix
from .router.decision import Rejected
from .router.state import (
    MAX_FIX_ATTEMPTS,
    ONE_SHOT_ACTIONS,
    REPEATED_ACTION,
    RouterState,
    state_for,
)
from .rules import AgentRulePack, VerificationLedger, load_ledger, load_pack
from .tracing import start_conversation, start_tool, start_turn, traced

MAX_PASSES = 6
"""A ceiling on the loop, whatever the router keeps asking for.

The stopping rules in plan section 5 cover every case the router cooperates
with. This covers the case where it does not.
"""

AGENT_NAME = "standardphysics-loop"

TOOL_NAMES: dict[RouterAction, str] = {
    "RESCAN_AREA": "rescan",
    "ASK_OWNER": "ask",
    "ESCALATE": "escalate",
    "DONE": "done",
}
"""The tool span each branch records. FIX records its search and its gate as two."""


@dataclass(frozen=True)
class StepResult:
    graph: SceneGraph
    proposal: Proposal | None = None
    gate: GateResult | None = None
    question: str | None = None
    escalated: tuple[UUID, ...] = ()
    rescan: tuple[UUID, ...] = ()
    message: str = ""
    fix_failed: bool = False
    search: FixOutcome | None = None


@dataclass(frozen=True)
class LoopStep:
    pass_number: int
    assessment: Assessment
    decision: Decision | None
    rejected: str | None
    action: RouterAction | None
    result: StepResult

    @property
    def authorized(self) -> bool:
        """Whether anything was allowed to happen at all."""
        return self.action is not None

    @property
    def graph(self) -> SceneGraph:
        return self.result.graph

    @property
    def message(self) -> str:
        return self.result.message


@dataclass
class Loop:
    """Everything one run of the loop carries between passes."""

    graph: SceneGraph
    scenario: Scenario
    measure: MeasurementProvider
    router: object
    rules: AgentRulePack = field(default_factory=load_pack)
    ledger: VerificationLedger = field(default_factory=load_ledger)
    max_tier: Tier = 1
    candidate_rejection: CandidateRejection | None = None
    actions_taken: tuple[RouterAction, ...] = ()
    fix_attempts: int = 0
    initial_pass: Pass | None = None
    fix_handler: Callable[["Loop", Pass, Decision], StepResult] | None = None

    one_shots_taken: frozenset[tuple[RouterAction, str]] = frozenset()
    """Each one-shot action this run has taken, paired with the layout hash."""

    last_gate: dict | None = None
    last_search: dict | None = None

    def look(self, pass_number: int) -> Pass:
        if pass_number == 1 and self.initial_pass is not None:
            return self.initial_pass
        return assess(
            self.graph,
            self.scenario,
            self.measure,
            rules=self.rules,
            ledger=self.ledger,
            pass_number=pass_number,
            max_tier=self.max_tier,
        )

    def state(self, current: Pass) -> RouterState:
        return state_for(
            current.findings,
            self.graph,
            self.rules,
            pass_number=current.assessment.pass_number,
            fix_attempts=self.fix_attempts,
            unevaluated=tuple(gap.rule_id for gap in current.unevaluated),
            actions_taken=self.actions_taken,
            last_gate=self.last_gate,
            last_search=self.last_search,
        )


def _targeted(current: Pass, decision: Decision) -> list:
    wanted = set(decision.target_finding_ids)
    return [finding for finding in current.findings if finding.id in wanted]


def _record(span: Any, **fields: Any) -> None:
    span.result = json.dumps(fields)


def _gate_brief(gate: GateResult) -> dict[str, Any]:
    return {
        "accepted": gate.accepted,
        "problems_before": gate.problems_before,
        "problems_after": gate.problems_after,
        "shortfall_before_in": round(gate.shortfall_before, 1),
        "shortfall_after_in": round(gate.shortfall_after, 1),
        "reasons": list(gate.reasons),
    }


def _search_brief(outcome: FixOutcome) -> dict[str, Any]:
    return {
        "found": outcome.found,
        "measured": outcome.measured,
        "rejected_constraints": list(outcome.rejected),
        "target_finding_ids": [str(target) for target in outcome.targets],
        "message": outcome.message,
    }


@traced("loop.fix")
def _do_fix(loop: Loop, current: Pass, decision: Decision) -> StepResult:
    with start_tool(name="propose_fix") as tool:
        outcome = propose_fix(
            loop.graph,
            loop.scenario,
            loop.measure,
            _targeted(current, decision),
            rules=loop.rules,
            ledger=loop.ledger,
            baseline=current,
            max_tier=loop.max_tier,
            candidate_rejection=loop.candidate_rejection,
        )
        _record(tool, **_search_brief(outcome))
    if not outcome.found or outcome.graph is None:
        return StepResult(
            graph=loop.graph, message=outcome.message, fix_failed=True, search=outcome
        )

    with start_tool(name="gate") as tool:
        after = assess(
            outcome.graph,
            loop.scenario,
            loop.measure,
            rules=loop.rules,
            ledger=loop.ledger,
            pass_number=current.assessment.pass_number,
            max_tier=loop.max_tier,
        )
        gate = accepts(current, after)
        _record(tool, **_gate_brief(gate))
    if not gate.accepted:
        return StepResult(
            graph=loop.graph,
            proposal=outcome.proposal,
            gate=gate,
            message="; ".join(gate.reasons),
            fix_failed=True,
            search=outcome,
        )
    return StepResult(
        graph=outcome.graph,
        proposal=outcome.proposal,
        gate=gate,
        message=outcome.proposal.rationale,
        search=outcome,
    )


@traced("loop.rescan")
def _do_rescan(loop: Loop, current: Pass, decision: Decision) -> StepResult:
    targets = _targeted(current, decision)
    return StepResult(
        graph=loop.graph,
        rescan=tuple(finding.id for finding in targets),
        message=targets[0].title if targets else "",
    )


@traced("loop.ask")
def _do_ask(loop: Loop, current: Pass, decision: Decision) -> StepResult:
    return StepResult(
        graph=loop.graph, question=decision.question, message=decision.question or ""
    )


@traced("loop.escalate")
def _do_escalate(loop: Loop, current: Pass, decision: Decision) -> StepResult:
    targets = _targeted(current, decision)
    return StepResult(
        graph=loop.graph,
        escalated=tuple(finding.id for finding in targets),
        message=escalation_note(len(targets)),
    )


@traced("loop.done")
def _do_done(loop: Loop, current: Pass, decision: Decision) -> StepResult:
    return StepResult(graph=loop.graph, message=REPORT_READY)


Handler = Callable[[Loop, Pass, Decision], StepResult]

HANDLERS: dict[RouterAction, Handler] = {
    "FIX": _do_fix,
    "RESCAN_AREA": _do_rescan,
    "ASK_OWNER": _do_ask,
    "ESCALATE": _do_escalate,
    "DONE": _do_done,
}


def _look(loop: Loop, pass_number: int) -> Pass:
    with start_tool(name="assess") as tool:
        current = loop.look(pass_number)
        _record(
            tool,
            problems=len(current.problems),
            questions=len(current.questions),
            graph_hash=current.assessment.graph_hash,
        )
    return current



def _handle(loop: Loop, current: Pass, decision: Decision) -> StepResult:
    handler = loop.fix_handler if decision.action == "FIX" and loop.fix_handler else HANDLERS[decision.action]
    tool_name = TOOL_NAMES.get(decision.action)
    if tool_name is None:
        return handler(loop, current, decision)
    with start_tool(name=tool_name) as tool:
        result = handler(loop, current, decision)
        _record(
            tool,
            action=decision.action,
            problems=len(current.problems),
            targets=len(decision.target_finding_ids),
        )
    return result


def _repeats_a_one_shot(loop: Loop, current: Pass, decision: Decision) -> bool:
    taken = (decision.action, current.assessment.graph_hash)
    return decision.action in ONE_SHOT_ACTIONS and taken in loop.one_shots_taken


def _refused(loop: Loop, current: Pass, pass_number: int, reason: str) -> LoopStep:
    return LoopStep(
        pass_number=pass_number,
        assessment=current.assessment,
        decision=None,
        rejected=reason,
        action=None,
        result=StepResult(graph=loop.graph),
    )


@traced("loop.pass")
def run_pass(loop: Loop, pass_number: int = 1) -> LoopStep:
    """Measure, decide, and do the one thing the decision authorizes.

    An answer that repeats a one-shot action on the layout it was already taken
    on authorizes nothing, the same as an answer that does not validate.
    """
    with start_turn(user_message=f"Fix what furniture can. Pass {pass_number}."):
        current = _look(loop, pass_number)
        answer = loop.router.decide(loop.state(current))
        if isinstance(answer, Rejected):
            return _refused(loop, current, pass_number, answer.reason)
        if _repeats_a_one_shot(loop, current, answer):
            return _refused(loop, current, pass_number, REPEATED_ACTION)
        return LoopStep(
            pass_number=pass_number,
            assessment=current.assessment.model_copy(update={"decision": answer}),
            decision=answer,
            rejected=None,
            action=answer.action,
            result=_handle(loop, current, answer),
        )


def _one_shots_after(loop: Loop, step: LoopStep) -> frozenset[tuple[RouterAction, str]]:
    if step.action not in ONE_SHOT_ACTIONS:
        return loop.one_shots_taken
    return loop.one_shots_taken | {(step.action, step.assessment.graph_hash)}


def _last_fix_after(loop: Loop, step: LoopStep) -> dict[str, dict | None]:
    """What the router next hears about the most recent search and gate."""
    search = step.result.search
    if search is None:
        return {"last_gate": loop.last_gate, "last_search": loop.last_search}
    gate = step.result.gate
    return {
        "last_gate": _gate_brief(gate) if gate is not None else None,
        "last_search": _search_brief(search),
    }


def _advance(loop: Loop, step: LoopStep) -> Loop:
    if step.action is None:
        return loop
    return replace(
        loop,
        graph=step.graph,
        actions_taken=(*loop.actions_taken, step.action),
        fix_attempts=loop.fix_attempts + (1 if step.result.fix_failed else 0),
        one_shots_taken=_one_shots_after(loop, step),
        **_last_fix_after(loop, step),
    )


def _stalled(steps: list[LoopStep]) -> bool:
    """A pass that did the same thing to the same layout changed nothing."""
    if len(steps) < 2:
        return False
    last, previous = steps[-1], steps[-2]
    return (
        last.action == previous.action
        and last.assessment.graph_hash == previous.assessment.graph_hash
    )


def _stop(steps: list[LoopStep], loop: Loop) -> bool:
    """Plan section 5: the targeted findings clear, three failed proposals,
    a pass with no improvement, or a service error."""
    step = steps[-1]
    if step.rejected is not None:
        return True
    if step.action == "DONE":
        return True
    if loop.fix_attempts >= MAX_FIX_ATTEMPTS:
        return True
    return _stalled(steps)


@traced("loop.run")
def run_loop(
    graph: SceneGraph,
    scenario: Scenario,
    measure: MeasurementProvider,
    router,
    *,
    rules: AgentRulePack | None = None,
    ledger: VerificationLedger | None = None,
    max_tier: Tier = 1,
    max_passes: int = MAX_PASSES,
    candidate_rejection: CandidateRejection | None = None,
    initial_pass: Pass | None = None,
    fix_handler: Callable[[Loop, Pass, Decision], StepResult] | None = None,
) -> list[LoopStep]:
    return list(
        loop_steps(
            graph,
            scenario,
            measure,
            router,
            rules=rules,
            ledger=ledger,
            max_tier=max_tier,
            max_passes=max_passes,
            candidate_rejection=candidate_rejection,
            initial_pass=initial_pass,
            fix_handler=fix_handler,
        )
    )


def loop_steps(
    graph: SceneGraph,
    scenario: Scenario,
    measure: MeasurementProvider,
    router,
    *,
    rules: AgentRulePack | None = None,
    ledger: VerificationLedger | None = None,
    max_tier: Tier = 1,
    max_passes: int = MAX_PASSES,
    candidate_rejection: CandidateRejection | None = None,
    initial_pass: Pass | None = None,
    fix_handler: Callable[[Loop, Pass, Decision], StepResult] | None = None,
) -> Iterator[LoopStep]:
    """The same loop as `run_loop`, handing over each pass as soon as it finishes."""
    loop = Loop(
        graph=graph,
        scenario=scenario,
        measure=measure,
        router=router,
        rules=rules or load_pack(),
        ledger=ledger if ledger is not None else load_ledger(),
        max_tier=max_tier,
        candidate_rejection=candidate_rejection,
        initial_pass=initial_pass,
        fix_handler=fix_handler,
    )
    steps: list[LoopStep] = []
    with start_conversation(
        agent_name=AGENT_NAME,
        conversation_id=f"{graph.scan_id}:{graph.revision}",
        conversation_name=getattr(router, "provider", ""),
    ):
        for pass_number in range(1, max_passes + 1):
            step = run_pass(loop, pass_number)
            steps.append(step)
            loop = _advance(loop, step)
            if _stop(steps, loop):
                break
    return steps
