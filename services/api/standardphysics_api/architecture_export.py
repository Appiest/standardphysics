"""A revision-pinned, inspectable architectural plan export.

The drawing is deliberately a projection of the measured scene graph.  It does
not infer door swings, fixtures, or any other behaviour that the capture did
not record.  The accompanying ledger keeps the raw capture values beside the
display-only geometry used in the SVG.
"""

from __future__ import annotations

import io
import json
import math
import uuid
import zipfile
from collections.abc import Iterable
from typing import Any
from xml.sax.saxutils import quoteattr

from fastapi import FastAPI, Response
from standardphysics_contracts import Assessment, SceneGraph, SceneNode, bounds_the_room, graph_hash, stands_upright

from . import repository as repo
from .db import Database
from .errors import ApiProblem

_PORTALS = frozenset({"door", "window", "opening"})
_DRAWING_SCALE_PX_PER_METER = 100.0
_DRAWING_MARGIN_METERS = 1.0


def _a_standing_surface(node: SceneNode) -> bool:
    """A sheet standing up that is a surface rather than a hole cut in one.

    A door and a window are also upright sheets, and they are drawn as the gaps
    they are rather than as more wall.
    """
    return stands_upright(node) and node.kind not in _PORTALS


def _number(value: float) -> float:
    """Keep JSON finite and SVG coordinates readable without changing source data."""
    if not math.isfinite(value):
        raise ValueError("scene geometry must be finite")
    return round(value, 6)


def _svg_number(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".") or "0"


def _world_point(node: SceneNode, x: float, y: float, z: float) -> tuple[float, float, float]:
    """Apply the row-major, Z-up node transform to one local point."""
    m = node.transform.m
    return (
        m[0] * x + m[1] * y + m[2] * z + m[3],
        m[4] * x + m[5] * y + m[6] * z + m[7],
        m[8] * x + m[9] * y + m[10] * z + m[11],
    )


def _corners(node: SceneNode) -> list[tuple[float, float, float]]:
    half = (node.dimensions.x / 2, node.dimensions.y / 2, node.dimensions.z / 2)
    return [
        _world_point(node, sx * half[0], sy * half[1], sz * half[2])
        for sx in (-1, 1)
        for sy in (-1, 1)
        for sz in (-1, 1)
    ]


def _convex_hull(points: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    """Monotonic-chain hull so an arbitrarily rotated box remains a true XY footprint."""
    unique = sorted({(_number(x), _number(y)) for x, y in points})
    if len(unique) <= 2:
        return unique

    def cross(origin, a, b) -> float:
        return (a[0] - origin[0]) * (b[1] - origin[1]) - (a[1] - origin[1]) * (b[0] - origin[0])

    lower: list[tuple[float, float]] = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float]] = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def footprint_xy_m(node: SceneNode) -> list[tuple[float, float]]:
    """The top-down footprint after the full Z-up transform, in metres."""
    return _convex_hull((x, y) for x, y, _ in _corners(node))


def _axis_xy(node: SceneNode, local_axis: int) -> tuple[float, float]:
    m = node.transform.m
    return (m[local_axis], m[4 + local_axis])


def _dot(point: tuple[float, float], axis: tuple[float, float]) -> float:
    return point[0] * axis[0] + point[1] * axis[1]


def _portal_cut_xy_m(portal: SceneNode, wall: SceneNode) -> list[tuple[float, float]]:
    """A wall-local opening cut shown in plan view.

    An opening can have zero physical thickness, which projects to a line.  For
    the drawing only, that line is expanded through its measured parent wall's
    thickness.  Its span and centre still come from the portal transform.
    """
    axes = [_axis_xy(wall, axis) for axis in range(3)]
    lengths = [math.hypot(*axis) for axis in axes]
    along_index = max(range(3), key=lambda index: lengths[index] * wall.dimensions.as_tuple()[index])
    along_length = lengths[along_index]
    if along_length <= 1e-9:
        return footprint_xy_m(portal)
    along = (axes[along_index][0] / along_length, axes[along_index][1] / along_length)
    normal = (-along[1], along[0])
    wall_footprint = footprint_xy_m(wall)
    normal_extent = max(_dot(point, normal) for point in wall_footprint) - min(
        _dot(point, normal) for point in wall_footprint
    )
    portal_footprint = footprint_xy_m(portal)
    portal_extent = max(_dot(point, along) for point in portal_footprint) - min(
        _dot(point, along) for point in portal_footprint)
    if portal_extent <= 1e-9:
        return portal_footprint
    center = portal.transform.position
    half_along, half_normal = portal_extent / 2, normal_extent / 2
    return [
        (
            center.x + along[0] * sx * half_along + normal[0] * sy * half_normal,
            center.y + along[1] * sx * half_along + normal[1] * sy * half_normal,
        )
        for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))
    ]


def _display_geometry(graph: SceneGraph) -> dict[uuid.UUID, dict[str, Any]]:
    by_id = {node.id: node for node in graph.nodes}
    display: dict[uuid.UUID, dict[str, Any]] = {}
    for node in graph.nodes:
        geometry: dict[str, Any] = {
            "type": "top_down_xy_footprint",
            "units": "m",
            "points": [[_number(x), _number(y)] for x, y in footprint_xy_m(node)],
            "generated_from": "current_scene_revision.transform_and_dimensions",
            "display_only": True,
        }
        if node.kind in _PORTALS and node.parent_id in by_id and stands_upright(by_id[node.parent_id]):
            geometry["wall_opening_cut"] = [
                [_number(x), _number(y)] for x, y in _portal_cut_xy_m(node, by_id[node.parent_id])
            ]
            geometry["wall_opening_cut_note"] = (
                "A zero-thickness portal is expanded only across its measured parent wall for plan readability."
            )
        display[node.id] = geometry
    return display


def _raw_node_reasons(raw_graph: dict[str, Any]) -> dict[str, str]:
    """Only carry a source-supplied placement rationale; never create one from labels."""
    reasons: dict[str, str] = {}
    for raw in raw_graph.get("nodes", []):
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        reason = raw.get("placement_rationale", raw.get("placement_reason"))
        if isinstance(reason, str) and reason.strip():
            reasons[str(raw["id"])] = reason.strip()
    return reasons


def _capture_facts(node: SceneNode | None) -> dict[str, Any]:
    """Facts only revision 0 can call captured; later layout edits cannot."""
    if node is None:
        return {
            "status": "unknown",
            "note": "No matching node exists in capture revision 0, so its raw capture facts are unknown.",
        }
    return {
        "status": "captured_from_revision_0",
        "dimensions_m": node.dimensions.model_dump(mode="json"),
        "transform_z_up_row_major": node.transform.m,
        "quality": node.quality,
        "quality_note": "Capture quality is not a certification of measurement precision.",
        "label_provenance": node.labeled_by,
        "parent_id": str(node.parent_id) if node.parent_id else None,
        "relation": node.relation,
    }


def _current_revision_geometry(node: SceneNode, revision: int) -> dict[str, Any]:
    """Current graph geometry may represent an owner rearrangement, not a capture fact."""
    return {
        "revision": revision,
        "dimensions_m": node.dimensions.model_dump(mode="json"),
        "transform_z_up_row_major": node.transform.m,
        "provenance": "current_scene_revision",
    }


def _display_reconstruction(node: SceneNode) -> dict[str, Any] | None:
    if node.reconstruction is None:
        return None
    reconstruction = node.reconstruction
    return {
        "display_only": True,
        "geometry": reconstruction.model_dump(mode="json"),
        "provenance": {
            "source": reconstruction.source,
            "confidence": reconstruction.confidence,
            "evidence_frame_ids": reconstruction.evidence_frame_ids,
        },
        "note": "Reconstruction geometry is visual completion, not a recovered measurement or placement fact.",
    }


def evidence_ledger(
    scan_id: uuid.UUID,
    scan_name: str,
    graph: SceneGraph,
    assessment: Assessment | None,
    source_capture: SceneGraph | None = None,
    source_capture_raw_graph: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Machine-readable evidence, with a firm line between observation and law."""
    current_hash = graph_hash(graph)
    display = _display_geometry(graph)
    captured_by_id = {node.id: node for node in source_capture.nodes} if source_capture is not None else {}
    reasons = _raw_node_reasons(source_capture_raw_graph or {})
    nodes = []
    for node in sorted(graph.nodes, key=lambda item: str(item.id)):
        entry = {
            "id": str(node.id),
            "kind": node.kind,
            "label": node.label,
            "raw_capture": _capture_facts(captured_by_id.get(node.id)),
            "current_revision_geometry": _current_revision_geometry(node, graph.revision),
            "original_placement_rationale": reasons.get(str(node.id), "unknown"),
            "generated_display_geometry": display[node.id],
        }
        reconstruction = _display_reconstruction(node)
        if reconstruction is not None:
            entry["display_reconstruction"] = reconstruction
        nodes.append(entry)
    findings = []
    if assessment is not None and assessment.graph_hash == current_hash:
        for finding in sorted(assessment.findings, key=lambda item: str(item.id)):
            finding_data: dict[str, Any] = {
                "id": str(finding.id),
                "check_id": finding.check_id,
                "outcome": finding.outcome,
                "title": finding.title,
                "measurement_result": (
                    {"value": finding.measured_inches, "unit": "in", "status": "recorded"}
                    if finding.measured_inches is not None
                    else {"status": "not_recorded"}
                ),
                "legal_requirement": {
                    "citation": finding.citation.model_dump(mode="json"),
                    "value": finding.required_inches,
                    "unit": "in",
                    "status": "recorded" if finding.required_inches is not None else "not_recorded",
                },
            }
            if finding.locus is not None:
                finding_data["evidence_node_ids"] = [str(node_id) for node_id in finding.locus.node_ids]
            findings.append(finding_data)
    return {
        "format": "standardphysics.architecture-evidence-ledger.v1",
        "scene": {
            "scan_id": str(scan_id),
            "scan_name": scan_name,
            "revision": graph.revision,
            "graph_hash": current_hash,
            "coordinate_system": {"units": "m", "up_axis": "Z", "transform_layout": "row_major_4x4"},
        },
        "measurement_notice": (
            "Only revision 0 facts are called raw capture. Current revision geometry can include owner layout changes. "
            "Neither display values nor capture quality certify exact centimetre measurements."
        ),
        "nodes": nodes,
        "assessment": {
            "included": assessment is not None and assessment.graph_hash == current_hash,
            "revision": assessment.graph_revision if assessment is not None else None,
            "findings": findings,
            "note": (
                "A measurement result records what the assessment measured; "
                "a legal requirement records a cited rule."
            )
        },
    }


def _path(points: list[tuple[float, float]], project) -> str:
    if not points:
        return ""
    commands = []
    for index, point in enumerate(points):
        x, y = project(point)
        commands.append(("M" if index == 0 else "L") + _svg_number(x) + " " + _svg_number(y))
    return " ".join(commands) + " Z"


def architecture_svg(graph: SceneGraph, ledger: dict[str, Any]) -> str:
    """Make a scalable plan whose one SVG unit remains tied to measured metres."""
    display = _display_geometry(graph)
    all_points = [tuple(point) for geometry in display.values() for point in geometry["points"]]
    if not all_points:
        all_points = [(0.0, 0.0)]
    min_x = min(point[0] for point in all_points) - _DRAWING_MARGIN_METERS
    max_x = max(point[0] for point in all_points) + _DRAWING_MARGIN_METERS
    min_y = min(point[1] for point in all_points) - _DRAWING_MARGIN_METERS
    max_y = max(point[1] for point in all_points) + _DRAWING_MARGIN_METERS
    width = max(1.0, (max_x - min_x) * _DRAWING_SCALE_PX_PER_METER)
    height = max(1.0, (max_y - min_y) * _DRAWING_SCALE_PX_PER_METER)

    def project(point: tuple[float, float]) -> tuple[float, float]:
        return ((point[0] - min_x) * _DRAWING_SCALE_PX_PER_METER, (max_y - point[1]) * _DRAWING_SCALE_PX_PER_METER)

    nodes = sorted(graph.nodes, key=lambda item: str(item.id))
    paths = {node.id: _path([tuple(point) for point in display[node.id]["points"]], project) for node in nodes}
    opening_cuts = {
        node.id: _path([tuple(point) for point in display[node.id].get("wall_opening_cut", [])], project)
        for node in nodes if "wall_opening_cut" in display[node.id]
    }
    masks = []
    for wall in (node for node in nodes if _a_standing_surface(node)):
        cuts = [opening_cuts[node.id] for node in nodes if node.parent_id == wall.id and node.id in opening_cuts]
        if not cuts:
            continue
        # The four-pixel mask stroke is display-only. It makes a zero-thickness
        # measured wall and portal read as an opening without changing ledger geometry.
        cut_paths = "".join(
            f'<path d={quoteattr(path)} fill="#000" stroke="#000" stroke-width="4"/>' for path in cuts
        )
        masks.append(
            f'<mask id={quoteattr("cut-" + str(wall.id))} maskUnits="userSpaceOnUse" x="0" y="0" '
            f'width={quoteattr(_svg_number(width))} height={quoteattr(_svg_number(height))}>'
            f'<rect width="100%" height="100%" fill="#fff"/>{cut_paths}</mask>'
        )
    wall_paths = []
    other_paths = []
    labels = []
    for node in nodes:
        if not paths[node.id]:
            continue
        extra = f' mask={quoteattr("url(#cut-" + str(node.id) + ")")}' if _a_standing_surface(node) and any(
            portal.parent_id == node.id and portal.id in opening_cuts for portal in nodes
        ) else ""
        element = (
            f'<path class={quoteattr(node.kind)} data-node-id={quoteattr(str(node.id))} '
            f'd={quoteattr(paths[node.id])}{extra}/>'
        )
        if _a_standing_surface(node):
            wall_paths.append(element)
        elif node.kind in _PORTALS:
            other_paths.append(
                f'<path class="opening" data-node-id={quoteattr(str(node.id))} '
                f'd={quoteattr(paths[node.id])}/>'
            )
        else:
            other_paths.append(element)
        if not bounds_the_room(node):
            center = node.transform.position
            x, y = project((center.x, center.y))
            labels.append(
                f'<text class="label" x={quoteattr(_svg_number(x))} y={quoteattr(_svg_number(y))}>'
                f'{quoteattr(node.label)[1:-1]}</text>'
            )
    scale_length = _DRAWING_SCALE_PX_PER_METER
    scale_y = height - 30
    metadata = json.dumps({"scan_id": ledger["scene"]["scan_id"], "revision": graph.revision,
                           "graph_hash": ledger["scene"]["graph_hash"]}, sort_keys=True, separators=(",", ":"))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_svg_number(width)} {_svg_number(height)}" '
        f'width="{_svg_number(width)}" height="{_svg_number(height)}" role="img" '
        'aria-label="Architectural floor plan in metres">'
        f'<metadata>{quoteattr(metadata)[1:-1]}</metadata><defs>'
        f'<pattern id="meter-grid" width="{_svg_number(scale_length)}" '
        f'height="{_svg_number(scale_length)}" patternUnits="userSpaceOnUse">'
        f'<path d="M {_svg_number(scale_length)} 0 L 0 0 0 {_svg_number(scale_length)}" '
        f'fill="none" stroke="#dbe4eb" stroke-width="1"/></pattern>{"".join(masks)}</defs>'
        f'<style>.wall{{fill:#263238;stroke:#102027;stroke-width:1}}.floor{{fill:none;stroke:#607d8b;stroke-width:1.5}}'
        f'.object{{fill:#d8c3a5;stroke:#5d4037;stroke-width:1.2}}.opening{{fill:none;stroke:#ef6c00;stroke-width:2}}'
        f'.label{{font:12px sans-serif;text-anchor:middle;dominant-baseline:middle;fill:#263238;'
        'pointer-events:none}}</style>'
        f'<rect width="100%" height="100%" fill="#fff"/><rect width="100%" height="100%" fill="url(#meter-grid)"/>'
        f'<g aria-label="Measured walls">{"".join(wall_paths)}</g>'
        f'<g aria-label="Measured scene">{"".join(other_paths)}</g>'
        f'<g aria-label="Object labels">{"".join(labels)}</g><g class="scale-bar" aria-label="Scale: 1 metre">'
        f'<path d="M 30 {_svg_number(scale_y)} L {_svg_number(30 + scale_length)} '
        f'{_svg_number(scale_y)}" stroke="#000" stroke-width="3"/>'
        f'<path d="M 30 {_svg_number(scale_y - 5)} L 30 {_svg_number(scale_y + 5)} '
        f'M {_svg_number(30 + scale_length)} {_svg_number(scale_y - 5)} '
        f'L {_svg_number(30 + scale_length)} {_svg_number(scale_y + 5)}" '
        'stroke="#000" stroke-width="2"/>'
        f'<text x="{_svg_number(30 + scale_length / 2)}" y="{_svg_number(scale_y - 8)}" class="label">1 m</text></g>'
        '</svg>'
    )


def build_architecture_zip(
    scan_id: uuid.UUID,
    scan_name: str,
    graph: SceneGraph,
    assessment: Assessment | None = None,
    source_capture: SceneGraph | None = None,
    source_capture_raw_graph: dict[str, Any] | None = None,
) -> bytes:
    """Create a deterministic ZIP for one already-stored scene revision."""
    ledger = evidence_ledger(
        scan_id, scan_name, graph, assessment, source_capture, source_capture_raw_graph
    )
    files = {
        "architecture-plan.svg": architecture_svg(graph, ledger).encode("utf-8"),
        "evidence-ledger.json": (
            json.dumps(ledger, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        ).encode("utf-8"),
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, files[name])
    return output.getvalue()


def install_architecture_export_routes(app: FastAPI, database: Database) -> None:
    """Install the protected ZIP endpoint; scan ownership is enforced by auth middleware."""
    @app.get("/api/scans/{scan_id}/architecture.zip")
    def download_architecture(scan_id: uuid.UUID, revision: int | None = None) -> Response:
        with database.connect() as connection:
            scan = repo.get_scan(connection, scan_id)
            if scan is None:
                raise ApiProblem(404, "no scan")
            row = repo.get_revision(connection, scan_id, revision)
            if row is None:
                raise ApiProblem(404, "not ready")
            graph = repo.graph_of(row)
            assessment = repo.assessment_for_revision(connection, scan_id, graph.revision)
            source_row = repo.get_revision(connection, scan_id, 0)
            source_capture = repo.graph_of(source_row) if source_row is not None else None
            source_capture_raw_graph = json.loads(source_row["graph_json"]) if source_row is not None else None
        archive = build_architecture_zip(
            scan_id, scan.name, graph, assessment, source_capture, source_capture_raw_graph
        )
        filename = f"architecture-{scan_id}-r{graph.revision}.zip"
        return Response(
            content=archive,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
