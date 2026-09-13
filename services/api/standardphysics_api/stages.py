"""Every stage a real capture goes through, each calling the lane that owns it.

    ingest    Lane B  parse_room_json
    label     Lane B  Astra label and clean, with a deterministic local fallback
    assess    Lane C  assess, with the human verification ledger
    geometry  Lane B  object-separated export_glb; scanned USDZ is fallback
    renders   Lane B  render_finding per locatable finding
    loop      Lane C  run_loop, routed by TypeSafe when configured

Swapping an implementation means changing one field of `Stages`. Geometry and
renders need Blender and run after the scan is ready, so a slow export never
holds up the findings.
"""

from __future__ import annotations

import json
import logging
import pathlib
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from standardphysics_agents import (
    LocalPolicyRouter,
    LoopStep,
    TypeSafeRouter,
    VerificationLedger,
    assess,
    load_ledger,
    load_pack,
    run_loop,
)
from standardphysics_agents.ask import Answer, ask
from standardphysics_agents.fix import FixOutcome, propose_fix
from standardphysics_contracts import Assessment, Finding, Scenario, SceneGraph, Stop, Vec3
from standardphysics_pipeline import PipelineMeasurements, blender, parse_room_json, reconstruct

log = logging.getLogger(__name__)

PREVIEW_REVIEWER = "unverified preview (development only)"

ROUTE_SUBJECTS = frozenset({"route", "route_leg", "route_turn", "route_dead_end"})

UNPLACED = Stop(name="Unplaced", position=Vec3(x=0.0, y=0.0, z=0.0))
NO_ROUTE_YET = Scenario(name="No route yet", stops=[UNPLACED, UNPLACED])
"""Lane C's CheckContext needs a scenario, and only rules that never read one run with this."""


def preview_ledger() -> VerificationLedger:
    ledger = VerificationLedger()
    for rule in load_pack().rules:
        ledger = ledger.record(rule, verified_by=PREVIEW_REVIEWER)
    return ledger


def configured_router() -> TypeSafeRouter | LocalPolicyRouter:
    """TypeSafe when its key and address are set, else Lane C's local policy, which says so on every decision."""
    router = TypeSafeRouter()
    return router if router.configured else LocalPolicyRouter()


def without_route_rules(ledger: VerificationLedger) -> VerificationLedger:
    """The same verifications minus every rule about a customer route, for a scan that has none yet."""
    route_rule_ids = {rule.id for rule in load_pack().rules if ROUTE_SUBJECTS.intersection(rule.applies_to)}
    return VerificationLedger(entries=[entry for entry in ledger.entries if entry.rule_id not in route_rule_ids])


@dataclass
class Stages:
    ledger_factory: Callable[[], VerificationLedger] = load_ledger
    measure: PipelineMeasurements = field(default_factory=PipelineMeasurements)
    label: Callable[[SceneGraph], SceneGraph] = reconstruct
    export_glb: Callable[[SceneGraph, pathlib.Path], pathlib.Path] = blender.export_glb
    usdz_to_glb: Callable[..., blender.ConversionResult] = blender.usdz_to_glb
    render_finding: Callable[..., pathlib.Path] = blender.render_finding
    router_factory: Callable[[], TypeSafeRouter | LocalPolicyRouter] = configured_router
    search_measure: PipelineMeasurements = field(default_factory=PipelineMeasurements)
    """A second cache for fix searches and questions, so neither holds up a drag check."""

    _assess_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _search_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def ingest(
        self,
        room_json: pathlib.Path,
        scan_id,
        *,
        frame_paths: list[pathlib.Path] | None = None,
        poses_path: pathlib.Path | None = None,
    ) -> SceneGraph:
        graph = parse_room_json(json.loads(room_json.read_bytes()), scan_id=scan_id)
        return self.label_scan(graph, frame_paths=frame_paths, poses_path=poses_path)

    def label_scan(
        self,
        graph: SceneGraph,
        *,
        frame_paths: list[pathlib.Path] | None = None,
        poses_path: pathlib.Path | None = None,
    ) -> SceneGraph:
        """Run the default Astra labeler with uploaded evidence when available.

        ``Stages.label`` remains a one-argument injection point for tests and
        callers with a custom labeler.  Only the built-in reconstruct function
        receives artifact paths, so adding photo evidence does not change that
        seam or expose paths to a custom implementation.
        """
        if self.label is reconstruct:
            return reconstruct(graph, frame_paths=frame_paths, poses_path=poses_path)
        return self.label(graph)

    def assess(self, graph: SceneGraph, scenario: Scenario | None, pass_number: int) -> Assessment:
        """Every verified rule, or only the rules that need no route until the owner confirms one."""
        ledger = self.ledger_factory()
        if scenario is None:
            ledger, scenario = without_route_rules(ledger), NO_ROUTE_YET
        with self._assess_lock:
            result = assess(graph, scenario, self.measure, ledger=ledger, pass_number=pass_number)
        for missing in result.unevaluated:
            log.info("rule %s not evaluated: %s", missing.rule_id, missing.waiting_on)
        checked = len(load_pack().enabled(ledger, max_tier=1))
        return result.assessment.model_copy(update={"rules_checked": checked})

    def propose(self, graph: SceneGraph, scenario: Scenario, targets: list[Finding]) -> FixOutcome:
        """Lane C's fix agent: one arrangement that clears the targets, or one thing to ask."""
        with self._search_lock:
            ledger = self.ledger_factory()
            return propose_fix(graph, scenario, self.search_measure, targets, rules=load_pack(), ledger=ledger)

    def loop(self, graph: SceneGraph, scenario: Scenario) -> tuple[str, list[LoopStep]]:
        """Lane C's loop on the search cache: the router's name, and every pass it ran."""
        router = self.router_factory()
        with self._search_lock:
            ledger = self.ledger_factory()
            steps = run_loop(graph, scenario, self.search_measure, router, rules=load_pack(), ledger=ledger)
        return router.provider, steps

    def ask(self, text: str, graph: SceneGraph, scenario: Scenario) -> Answer:
        """Lane C's ask box, on the search cache."""
        with self._search_lock:
            return ask(text, graph, scenario, self.search_measure, ledger=self.ledger_factory())

    def geometry(
        self,
        graph: SceneGraph,
        out: pathlib.Path,
        usdz: pathlib.Path | None,
        mapping: pathlib.Path | None,
    ) -> pathlib.Path | None:
        """Object-separated graph geometry, with a fully mapped scan as fallback."""
        try:
            return self.export_glb(graph, out)
        except (FileNotFoundError, blender.BlenderError) as exc:
            log.info("graph glb failed, trying scanned mesh: %s", exc)
        scanned = self._scanned_mesh(out, usdz, mapping)
        if scanned is not None:
            return scanned
        log.warning("no display geometry for %s", graph.scan_id)
        return None

    def _scanned_mesh(
        self, out: pathlib.Path, usdz: pathlib.Path | None, mapping: pathlib.Path | None
    ) -> pathlib.Path | None:
        if usdz is None or mapping is None:
            return None
        try:
            converted = self.usdz_to_glb(usdz, out, mapping)
        except (FileNotFoundError, blender.BlenderError) as exc:
            log.info("usdz_to_glb failed after graph export: %s", exc)
            return None
        if not converted.fully_identified:
            log.info("usdz_to_glb left %s meshes unmapped", converted.unmapped_count)
            return None
        return converted.glb_path

    def renders(self, graph: SceneGraph, assessment: Assessment, directory: pathlib.Path, url_for) -> Assessment:
        findings = [self._rendered(graph, finding, directory, url_for) for finding in assessment.findings]
        return assessment.model_copy(update={"findings": findings})

    def _rendered(self, graph, finding, directory: pathlib.Path, url_for):
        if finding.locus is None:
            return finding
        target = directory / f"{finding.id}.png"
        try:
            if not target.exists():
                self.render_finding(graph, finding.locus, target)
        except (FileNotFoundError, blender.BlenderError) as exc:
            log.warning("no render for finding %s: %s", finding.id, exc)
            return finding
        locus = finding.locus.model_copy(update={"render_url": url_for(finding.id)})
        return finding.model_copy(update={"locus": locus})
