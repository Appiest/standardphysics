"use client";

import { Lock } from "@phosphor-icons/react";
import { type KeyboardEvent, type PointerEvent, useId, useMemo, useRef } from "react";
import { drawnNodes, footprint, planBounds } from "@/components/FloorPlan";
import { displayName, floorHeight, isListed } from "@/lib/found-objects";
import type { StaffHandles } from "@/lib/staff-areas";
import type { Finding, SceneGraph, SceneNode, Vec3 } from "@/types/contracts";
import { STAFF_AREA_ATTRIBUTE, StaffPlanAreas } from "./StaffPlanAreas";
import { type PlanDragHandlers, usePlanDrag } from "./usePlanDrag";

/** Pieces smaller than this are hard to catch with a thumb, so their grab area grows to it. */
const SMALLEST_GRAB_METERS = 0.35;
const LOCK_SIZE_METERS = 0.28;
const LABEL_SIZE_METERS = 0.3;

export type PieceState = { activeId: string | null; blockedIds: Set<string>; pointedIds: Set<string>; movedIds: Set<string> };

type PlanProps = PieceState & PlanDragHandlers & {
  /** The layout being tried, drawn as it is now. */
  shown: SceneGraph;
  /** The scanned layout, which fixes the frame so the drawing doesn't slide while a piece moves. */
  scanned: SceneGraph;
  problems: Finding[];
  cleared: Finding[];
  onFixedTap: (nodeId: string) => void;
  onKey: (nodeId: string, event: KeyboardEvent) => void;
  /** The staff-only floor, drawn under the furniture. */
  staff: StaffHandles | null;
};

type Role = "movable" | "fixed" | "backdrop";

function roleOf(node: SceneNode, floor: number): Role {
  if (node.kind !== "object" || !isListed(node, floor)) return "backdrop";
  return node.movable ? "movable" : "fixed";
}

function placement(node: SceneNode) {
  const { x, y, width, depth, degrees } = footprint(node);
  return { width, depth, transform: `translate(${x.toFixed(4)}px, ${(-y).toFixed(4)}px) rotate(${(-degrees).toFixed(3)}deg)` };
}

/** Floor under walls under furniture, and what can be picked up on top, so a chair tucked under a table still catches the pointer. */
const LAYER: Record<string, number> = { floor: 0, object: 1, wall: 2, fixed: 3, movable: 4 };

function layerOf(node: SceneNode, floor: number): number {
  const role = roleOf(node, floor);
  return LAYER[role === "backdrop" ? node.kind : role] ?? LAYER.object;
}

/**
 * The shop from above, where furniture is picked up and set down. Walls and
 * the floor are drawn in ink; pieces that can move are solid and draggable,
 * built-in ones carry a hatch and a padlock, and the problems the tried layout
 * has are drawn as red dimension lines with their measurement.
 */
export function LayoutPlan(props: PlanProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const drag = usePlanDrag(svgRef, props);
  const hatchId = useId();
  const view = useMemo(() => viewBoxOf(props.scanned), [props.scanned]);
  const floor = useMemo(() => floorHeight(props.scanned), [props.scanned]);
  const nodes = useMemo(() => [...drawnNodes(props.shown)].sort((a, b) => layerOf(a, floor) - layerOf(b, floor)), [props.shown, floor]);
  const floors = nodes.filter((node) => node.kind === "floor");
  const above = nodes.filter((node) => node.kind !== "floor");
  return (
    <svg ref={svgRef} viewBox={view} onPointerDown={(event) => closeStaffUnlessOnIt(props.staff, event.target)} className="size-full touch-none select-none bg-paper" role="group" aria-label="Your shop from above. Drag a piece to move it, or focus one and use the arrow keys.">
      <defs>
        <pattern id={hatchId} width={0.08} height={0.08} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="0.08" stroke="var(--color-ink)" strokeWidth={0.015} strokeOpacity={0.5} />
        </pattern>
      </defs>
      {floors.map((node) => <Backdrop key={node.id} node={node} />)}
      {props.staff && <StaffPlanAreas handles={props.staff} svgRef={svgRef} />}
      {above.map((node) => {
        const role = roleOf(node, floor);
        if (role === "backdrop") return <Backdrop key={node.id} node={node} />;
        if (role === "fixed") return <FixedPiece key={node.id} node={node} hatch={`url(#${hatchId})`} onTap={props.onFixedTap} />;
        return <MovablePiece key={node.id} node={node} state={props} dragging={drag.draggingId === node.id} drag={drag} onKey={props.onKey} />;
      })}
      {props.cleared.map((finding) => <Dimension key={`cleared-${finding.id}`} finding={finding} tone="cleared" />)}
      {props.problems.map((finding) => <Dimension key={finding.id} finding={finding} tone="problem" />)}
    </svg>
  );
}

/** A tap anywhere on the plan but a staff area closes the open one, as a tap off it does in 3D. */
function closeStaffUnlessOnIt(staff: StaffHandles | null, target: EventTarget) {
  if (!staff || staff.chosen === null || staff.chosen === "all") return;
  if (!(target instanceof Element) || !target.closest(`[${STAFF_AREA_ATTRIBUTE}]`)) staff.onChoose(null);
}

function viewBoxOf(scene: SceneGraph): string {
  const box = planBounds(drawnNodes(scene));
  const pad = Math.max(box.width, box.height) * 0.05;
  return `${box.minX - pad} ${-(box.minY + box.height) - pad} ${box.width + 2 * pad} ${box.height + 2 * pad}`;
}

const BACKDROP_STYLE: Record<string, { fill: string; fillOpacity: number; stroke: string }> = {
  floor: { fill: "var(--color-sheet)", fillOpacity: 1, stroke: "var(--color-rule)" },
  wall: { fill: "var(--color-ink)", fillOpacity: 1, stroke: "var(--color-ink)" },
  object: { fill: "var(--color-ink)", fillOpacity: 0.08, stroke: "none" },
};

/** RoomPlan walls are planes with no thickness, which an SVG rect wouldn't draw at all. */
const THINNEST_WALL_METERS = 0.1;

function Backdrop({ node }: { node: SceneNode }) {
  const { width, depth: measured, transform } = placement(node);
  const depth = node.kind === "wall" ? Math.max(measured, THINNEST_WALL_METERS) : measured;
  const style = BACKDROP_STYLE[node.kind] ?? BACKDROP_STYLE.object;
  return <rect x={-width / 2} y={-depth / 2} width={width} height={depth} style={{ transform }} {...style} vectorEffect="non-scaling-stroke" aria-hidden />;
}

function FixedPiece({ node, hatch, onTap }: { node: SceneNode; hatch: string; onTap: (nodeId: string) => void }) {
  const { width, depth, transform } = placement(node);
  const { x, y } = footprint(node);
  const lockFits = Math.min(width, depth) >= LOCK_SIZE_METERS * 1.4;
  return (
    <g className="cursor-not-allowed" onPointerDown={() => onTap(node.id)}>
      <title>{`${displayName(node)} is built in and stays put`}</title>
      <g style={{ transform }}>
        <rect x={-width / 2} y={-depth / 2} width={width} height={depth} fill="var(--color-sheet)" stroke="var(--color-ink-muted)" strokeWidth={1} vectorEffect="non-scaling-stroke" />
        <rect x={-width / 2} y={-depth / 2} width={width} height={depth} fill={hatch} />
      </g>
      {lockFits && <LockGlyph x={x} y={-y} />}
    </g>
  );
}

/** The padlock, upright whatever way the piece faces, in a disc so it reads over the hatch. */
function LockGlyph({ x, y }: { x: number; y: number }) {
  const half = LOCK_SIZE_METERS / 2;
  return (
    <g aria-hidden transform={`translate(${x.toFixed(4)} ${y.toFixed(4)})`}>
      <circle r={half * 1.25} fill="var(--color-ink)" />
      <Lock x={-half * 0.7} y={-half * 0.7} size={LOCK_SIZE_METERS * 0.7} weight="bold" color="var(--color-paper)" />
    </g>
  );
}

type DragBinding = ReturnType<typeof usePlanDrag>;

function pieceTone(nodeId: string, state: PieceState): { fill: string; stroke: string; width: number } {
  if (state.blockedIds.has(nodeId)) return { fill: "var(--color-problem)", stroke: "var(--color-problem)", width: 2.5 };
  if (state.activeId === nodeId || state.pointedIds.has(nodeId)) return { fill: "var(--color-accent)", stroke: "var(--color-accent)", width: 2.5 };
  return { fill: "var(--color-ink)", stroke: "var(--color-ink)", width: 1.25 };
}

function MovablePiece({ node, state, dragging, drag, onKey }: { node: SceneNode; state: PieceState; dragging: boolean; drag: DragBinding; onKey: PlanProps["onKey"] }) {
  const { width, depth, transform } = placement(node);
  const tone = pieceTone(node.id, state);
  const [grabWidth, grabDepth] = [Math.max(width, SMALLEST_GRAB_METERS), Math.max(depth, SMALLEST_GRAB_METERS)];
  const moved = state.movedIds.has(node.id);
  return (
    <g
      role="button"
      tabIndex={0}
      aria-label={`Move the ${displayName(node).toLowerCase()}`}
      aria-pressed={state.activeId === node.id}
      style={{ transform }}
      className={`cursor-grab outline-none focus-visible:[&>rect:nth-child(2)]:stroke-accent ${dragging ? "cursor-grabbing" : "transition-transform duration-200 ease-[var(--ease-settle)] motion-reduce:transition-none"}`}
      onPointerDown={(event: PointerEvent<SVGGElement>) => drag.grab(node.id, event)}
      onPointerMove={drag.move}
      onPointerUp={drag.release}
      onPointerCancel={drag.release}
      onKeyDown={(event) => onKey(node.id, event)}
    >
      <rect x={-grabWidth / 2} y={-grabDepth / 2} width={grabWidth} height={grabDepth} fill="transparent" />
      <rect
        x={-width / 2} y={-depth / 2} width={width} height={depth}
        fill={tone.fill} fillOpacity={dragging || moved ? 0.32 : 0.18}
        stroke={tone.stroke} strokeWidth={tone.width} strokeDasharray={moved ? undefined : "4 3"}
        vectorEffect="non-scaling-stroke"
      />
    </g>
  );
}

const DIMENSION_TONE = {
  problem: { stroke: "var(--color-problem)", dash: undefined, opacity: 1 },
  cleared: { stroke: "var(--color-pass)", dash: "6 4", opacity: 0.9 },
};

function midpoint(points: Vec3[]): Vec3 {
  return points[Math.floor(points.length / 2)];
}

/** A measurement drawn where it was taken: red while it misses, dashed green once the layout clears it. */
function Dimension({ finding, tone }: { finding: Finding; tone: keyof typeof DIMENSION_TONE }) {
  const annotation = finding.locus?.annotation;
  if (!annotation || annotation.points.length < 2) return null;
  const style = DIMENSION_TONE[tone];
  const line = annotation.points.map((point) => `${point.x.toFixed(4)},${(-point.y).toFixed(4)}`).join(" ");
  const at = midpoint(annotation.points);
  return (
    <g aria-hidden opacity={style.opacity} className="pointer-events-none">
      <polyline points={line} fill="none" stroke={style.stroke} strokeWidth={3} strokeDasharray={style.dash} strokeLinecap="round" vectorEffect="non-scaling-stroke" />
      {tone === "problem" && annotation.label && (
        <text x={at.x} y={-at.y - LABEL_SIZE_METERS * 0.6} fontSize={LABEL_SIZE_METERS} textAnchor="middle" className="measurement" fill={style.stroke} stroke="var(--color-paper)" strokeWidth={LABEL_SIZE_METERS * 0.25} paintOrder="stroke">
          {annotation.label}
        </text>
      )}
    </g>
  );
}
