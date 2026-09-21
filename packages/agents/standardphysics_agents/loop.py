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
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable, Iterator
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
from .fix.search import CandidateRejection, propose_fix
from .router.decision import Rejected
from .router.state import MAX_FIX_ATTEMPTS, RouterState, state_for
from .rules import AgentRulePack, VerificationLedger, load_ledger, load_pack
from .tracing import traced

MAX_PASSES = 6
"""A ceiling on the loop, whatever the router keeps asking for.

The stopping rules in plan section 5 cover every case the router cooperates
with. This covers the case where it does not.
"""


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
        )


def _targeted(current: Pass, decision: Decision) -> list:
    wanted = set(decision.target_finding_ids)
    return [finding for finding in current.findings if finding.id in wanted]


@traced("loop.fix")
def _do_fix(loop: Loop, current: Pass, decision: Decision) -> StepResult:
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
    if not outcome.found or outcome.graph is None:
        return StepResult(
            graph=loop.graph, message=outcome.message, fix_failed=True
        )

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
    if not gate.accepted:
        return StepResult(
            graph=loop.graph,
            proposal=outcome.proposal,
            gate=gate,
            message="; ".join(gate.reasons),
            fix_failed=True,
        )
    return StepResult(
        graph=outcome.graph,
        proposal=outcome.proposal,
        gate=gate,
        message=outcome.proposal.rationale,
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


@traced("loop.pass")
def run_pass(loop: Loop, pass_number: int = 1) -> LoopStep:
    """Measure, decide, and do the one thing the decision authorizes."""
    current = loop.look(pass_number)
    answer = loop.router.decide(loop.state(current))

    if isinstance(answer, Rejected):
        return LoopStep(
            pass_number=pass_number,
            assessment=current.assessment,
            decision=None,
            rejected=answer.reason,
            action=None,
            result=StepResult(graph=loop.graph),
        )

    handler = loop.fix_handler if answer.action == "FIX" and loop.fix_handler else HANDLERS[answer.action]
    result = handler(loop, current, answer)
    return LoopStep(
        pass_number=pass_number,
        assessment=current.assessment.model_copy(update={"decision": answer}),
        decision=answer,
        rejected=None,
        action=answer.action,
        result=result,
    )


def _advance(loop: Loop, step: LoopStep) -> Loop:
    if step.action is None:
        return loop
    return replace(
        loop,
        graph=step.graph,
        actions_taken=(*loop.actions_taken, step.action),
        fix_attempts=loop.fix_attempts + (1 if step.result.fix_failed else 0),
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
    for pass_number in range(1, max_passes + 1):
        step = run_pass(loop, pass_number)
        steps.append(step)
        loop = _advance(loop, step)
        if _stop(steps, loop):
            break
    return steps
