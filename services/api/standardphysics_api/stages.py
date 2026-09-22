"""Every stage a real capture goes through, each calling the lane that owns it.

    ingest    Lane B  parse_room_json
    label     Lane B  Astra label and clean, with a deterministic local fallback
    discover  Lane B  the objects RoomPlan has no category for, found in the LiDAR
    assess    Lane C  assess, with the human verification ledger
    geometry  Lane B  object-separated export_glb; scanned USDZ is fallback
    renders   Lane B  render_finding per locatable finding
    loop      Lane C  loop_steps, routed by TypeSafe when configured

Swapping an implementation means changing one field of `Stages`. Geometry and
renders need Blender and run after the scan is ready, so a slow export never
holds up the findings.
"""

from __future__ import annotations

import json
import logging
import pathlib
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from standardphysics_agents import (
    LocalPolicyRouter,
    LoopStep,
    TypeSafeRouter,
    VerificationLedger,
    assess,
    load_ledger,
    load_pack,
)
from standardphysics_agents.ask import Answer, ask
from standardphysics_agents.fix import FixOutcome, propose_fix
from standardphysics_agents.loop import loop_steps
from standardphysics_contracts import Assessment, Finding, Scenario, SceneGraph, Stop, Vec3
from standardphysics_pipeline import PipelineMeasurements, blender, parse_room_json, reconstruct
from standardphysics_pipeline.discovery import DiscoveryError, DiscoveryInputs, DiscoveryResult, discover_objects
from standardphysics_pipeline.textures import BakeInputs, BakeResult, bake_textures

from .scope_manifest import build_scope_manifest

log = logging.getLogger(__name__)

PREVIEW_REVIEWER = "unverified preview (development only)"

ROUTE_SUBJECTS = frozenset({"route", "route_leg", "route_turn", "route_dead_end"})

UNPLACED = Stop(name="Unplaced", position=Vec3(x=0.0, y=0.0, z=0.0))
NO_ROUTE_YET = Scenario(name="No route yet", stops=[UNPLACED, UNPLACED])
"""Lane C's CheckContext needs a scenario, and only rules that never read one run with this."""


def _discovery_inputs(
    graph: SceneGraph,
    frame_paths: list[pathlib.Path] | None,
    poses_path: pathlib.Path | None,
    lidar_mesh_path: pathlib.Path | None,
) -> DiscoveryInputs | None:
    """The photos, poses and mesh discovery needs, or nothing when the scan lacks one."""
    if not frame_paths or poses_path is None or lidar_mesh_path is None:
        return None
    if not poses_path.is_file() or not lidar_mesh_path.is_file():
        return None
    frames = {path.name: path for path in frame_paths if path.is_file()}
    if not frames:
        return None
    return DiscoveryInputs(
        graph=graph,
        poses_path=poses_path,
        frame_paths=frames,
        lidar_mesh_path=lidar_mesh_path,
        cache_dir=lidar_mesh_path.parent.parent / "detections",
        crop_dir=lidar_mesh_path.parent.parent / "crops",
    )


@dataclass
class DiscoveryOutcome:
    """What the discovery pass did, so an empty result is told apart from a failed one."""

    attempted: bool = False
    """False when discovery was deferred or had no usable inputs."""
    deferred_reason: str | None = None
    frames_read: int = 0
    object_count: int = 0
    people_points_removed: int = 0
    failures: list[str] = field(default_factory=list)
    model_requests: list = field(default_factory=list)
    """S's ModelRequestInfo per actual detector request, persisted for the trail."""

    def note(self) -> str | None:
        """One visible line for the job record; categories only, never secrets."""
        if self.deferred_reason is not None:
            return f"semantic discovery deferred: {self.deferred_reason}"
        if not self.attempted:
            return None
        parts = [
            f"read {self.frames_read} photos",
            f"found {self.object_count} objects",
        ]
        if self.failures:
            example = str(self.failures[0])[:200]
            parts.append(f"{len(self.failures)} frame(s) unread, e.g. {example}")
        return "discovery: " + ", ".join(parts)


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
    bake_textures: Callable[[BakeInputs], BakeResult] = bake_textures
    ledger_factory: Callable[[], VerificationLedger] = load_ledger
    measure: PipelineMeasurements = field(default_factory=PipelineMeasurements)
    label: Callable[[SceneGraph], SceneGraph] = reconstruct
    discover: Callable[[DiscoveryInputs], DiscoveryResult] = discover_objects
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
        lidar_mesh_path: pathlib.Path | None = None,
    ) -> SceneGraph:
        graph, _ = self.ingest_with_report(
            room_json,
            scan_id,
            frame_paths=frame_paths,
            poses_path=poses_path,
            lidar_mesh_path=lidar_mesh_path,
        )
        return graph

    def ingest_with_report(
        self,
        room_json: pathlib.Path,
        scan_id,
        *,
        frame_paths: list[pathlib.Path] | None = None,
        poses_path: pathlib.Path | None = None,
        lidar_mesh_path: pathlib.Path | None = None,
        run_discovery: bool = True,
    ) -> tuple[SceneGraph, DiscoveryOutcome]:
        """Ingest like `ingest`, plus what the discovery pass actually saw.

        The outcome lets the worker record why a graph has no new nodes: the
        photos are still uploading, discovery could not run, or it ran and
        legitimately found nothing.
        """
        graph = parse_room_json(json.loads(room_json.read_bytes()), scan_id=scan_id)
        graph = self.label_scan(graph, frame_paths=frame_paths, poses_path=poses_path, lidar_mesh_path=lidar_mesh_path)
        if not run_discovery:
            return graph, DiscoveryOutcome()
        return self.discover_scan_with_report(
            graph, frame_paths=frame_paths, poses_path=poses_path, lidar_mesh_path=lidar_mesh_path
        )

    def discover_scan(
        self,
        graph: SceneGraph,
        *,
        frame_paths: list[pathlib.Path] | None,
        poses_path: pathlib.Path | None,
        lidar_mesh_path: pathlib.Path | None,
    ) -> SceneGraph:
        """The same graph plus the objects RoomPlan has no category for.

        A scan with no photos, or one the vision model cannot reach, keeps the
        nodes RoomPlan measured. Discovery only ever adds.
        """
        graph, _ = self.discover_scan_with_report(
            graph, frame_paths=frame_paths, poses_path=poses_path, lidar_mesh_path=lidar_mesh_path
        )
        return graph

    def discover_scan_with_report(
        self,
        graph: SceneGraph,
        *,
        frame_paths: list[pathlib.Path] | None,
        poses_path: pathlib.Path | None,
        lidar_mesh_path: pathlib.Path | None,
    ) -> tuple[SceneGraph, DiscoveryOutcome]:
        inputs = _discovery_inputs(graph, frame_paths, poses_path, lidar_mesh_path)
        if inputs is None:
            return graph, DiscoveryOutcome()
        try:
            result = self.discover(inputs)
        except (DiscoveryError, OSError) as exc:
            log.warning("no object discovery for %s: %s", graph.scan_id, exc)
            return graph, DiscoveryOutcome(attempted=True, failures=[f"discovery could not run: {exc}"])
        for failure in result.failures:
            log.info("discovery could not read a frame %s: %s", graph.scan_id, failure)
        log.info(
            "discovered %d objects for %s, and took %d mesh points of people out",
            len(result.nodes),
            graph.scan_id,
            result.people_points_removed,
        )
        existing_ids = {n.id for n in result.nodes}
        preserved = [n for n in graph.nodes if n.id not in existing_ids]
        outcome = DiscoveryOutcome(
            attempted=True,
            frames_read=result.frames_read,
            object_count=len(result.nodes),
            people_points_removed=result.people_points_removed,
            failures=list(result.failures),
            model_requests=list(result.model_requests),
        )
        return graph.model_copy(update={"nodes": [*preserved, *result.nodes]}), outcome

    def label_scan(
        self,
        graph: SceneGraph,
        *,
        frame_paths: list[pathlib.Path] | None = None,
        poses_path: pathlib.Path | None = None,
        lidar_mesh_path: pathlib.Path | None = None,
        capture_graph: SceneGraph | None = None,
    ) -> SceneGraph:
        """Run the default Astra labeler with uploaded evidence when available.

        ``Stages.label`` remains a one-argument injection point for tests and
        callers with a custom labeler.  Only the built-in reconstruct function
        receives artifact paths, so adding photo evidence does not change that
        seam or expose paths to a custom implementation.
        """
        if self.label is reconstruct:
            captured = {node.id: node for node in capture_graph.nodes} if capture_graph else {}
            # Furniture may have moved since these photos were captured.
            evidence_graph = graph.model_copy(
                update={
                    "capture_to_room": graph.capture_to_room
                    or (capture_graph.capture_to_room if capture_graph else None),
                    "nodes": [
                        node.model_copy(update={"transform": captured[node.id].transform})
                        if node.id in captured
                        else node
                        for node in graph.nodes
                    ],
                }
            )
            result = reconstruct(
                evidence_graph, frame_paths=frame_paths, poses_path=poses_path, lidar_mesh_path=lidar_mesh_path
            )
            placements = {node.id: node.transform for node in graph.nodes}
            return result.model_copy(
                update={"nodes": [node.model_copy(update={"transform": placements[node.id]}) for node in result.nodes]}
            )
        return self.label(graph)

    def assess(self, graph: SceneGraph, scenario: Scenario | None, pass_number: int) -> Assessment:
        """Every verified rule, or only the rules that need no route until the owner confirms one.

        The frozen scope manifest rides on the assessment (contract 5): one
        visible outcome per requested requirement, with unevaluated checks as
        unobserved rows. Lane A hardens applicability behind it.
        """
        ledger = self.ledger_factory()
        pack = load_pack()
        if scenario is None:
            ledger, scenario = without_route_rules(ledger), NO_ROUTE_YET
        with self._assess_lock:
            result = assess(graph, scenario, self.measure, ledger=ledger, pass_number=pass_number)
        for missing in result.unevaluated:
            log.info("rule %s not evaluated: %s", missing.rule_id, missing.waiting_on)
        checked = len(pack.enabled(ledger, max_tier=1))
        waiting = {gap.rule_id: gap.waiting_on for gap in result.unevaluated}
        scope = build_scope_manifest(graph, scenario, result.assessment, pack.enabled(ledger, max_tier=1), waiting)
        return result.assessment.model_copy(update={"rules_checked": checked, "scope": scope})

    def propose(self, graph: SceneGraph, scenario: Scenario, targets: list[Finding]) -> FixOutcome:
        """Lane C's fix agent: one arrangement that clears the targets, or one thing to ask."""
        with self._search_lock:
            ledger = self.ledger_factory()
            return propose_fix(graph, scenario, self.search_measure, targets, rules=load_pack(), ledger=ledger)

    def loop(self, graph: SceneGraph, scenario: Scenario) -> tuple[str, Iterator[LoopStep]]:
        """Lane C's loop on the search cache: the router's name, and each pass as it finishes."""
        router = self.router_factory()
        return router.provider, self._loop_steps(graph, scenario, router)

    def _loop_steps(self, graph: SceneGraph, scenario: Scenario, router) -> Iterator[LoopStep]:
        with self._search_lock:
            ledger = self.ledger_factory()
            yield from loop_steps(graph, scenario, self.search_measure, router, rules=load_pack(), ledger=ledger)

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
