"use client";

import { Lock } from "@phosphor-icons/react";
import { type KeyboardEvent, type PointerEvent, useId, useMemo, useRef } from "react";
import { drawnNodes, footprint } from "@/components/FloorPlan";
import { displayName, floorHeight } from "@/lib/found-objects";
import { supportOf } from "@/lib/moves";
import {
  drawOrder, hasMoved, labelBesideLine, type PlanBox, type PlanLabel, placeLabels, planRole, planTextMeters, planTurnDegrees,
  turnedBounds, turnedHalfExtent, turnedPoint, viewBoxAttribute,
} from "@/lib/plan-view";
import type { StaffHandles } from "@/lib/staff-areas";
import type { Finding, SceneGraph, SceneNode, Vec3 } from "@/types/contracts";
import { STAFF_AREA_ATTRIBUTE, StaffPlanAreas, StaffPlanLabels } from "./StaffPlanAreas";
import { type PlanDragHandlers, usePlanDrag } from "./usePlanDrag";

/** Pieces smaller than this are hard to catch with a thumb, so their grab area grows to it. */
const SMALLEST_GRAB_METERS = 0.35;
const LOCK_SIZE_METERS = 0.28;

export type PieceState = { activeId: string | null; blockedIds: Set<string>; pointedIds: Set<string>; movedIds: Set<string> };

type PlanProps = PieceState & PlanDragHandlers & {
  /** The layout being tried, drawn as it is now. */
  shown: SceneGraph;
  /** The scanned layout, which fixes the frame so the drawing doesn't slide while a piece moves. */
  scanned: SceneGraph;
  problems: Finding[];
  cleared: Finding[];
  onFixedTap: (nodeId: string) => void;
  /** A key pressed on a piece, with the plan's turn so arrows can follow the screen. */
  onKey: (nodeId: string, event: KeyboardEvent, turnDegrees: number) => void;
  /** The staff-only floor, drawn under the furniture. */
  staff: StaffHandles | null;
};

/** How the scanned room sits on screen: turned square, fitted, with text sized to it. */
type PlanFrame = { turn: number; box: PlanBox; text: number; floor: number };

function frameOf(scanned: SceneGraph): PlanFrame {
  const turn = planTurnDegrees(scanned);
  const box = turnedBounds(drawnNodes(scanned), turn);
  return { turn, box, text: planTextMeters(box), floor: floorHeight(scanned) };
}

function placement(node: SceneNode) {
  const { x, y, width, depth, degrees } = footprint(node);
  return { width, depth, transform: `translate(${x.toFixed(4)}px, ${(-y).toFixed(4)}px) rotate(${(-degrees).toFixed(3)}deg)` };
}

/**
 * The shop from above, where furniture is picked up and set down. The room is
 * turned so its walls run square with the screen. Walls and the floor are drawn
 * in ink; pieces that can move are outlined and draggable, built-in ones carry a
 * hatch and a padlock, and the problems the tried layout has are drawn as red
 * dimension lines with their measurement. Words sit outside the turn so they
 * always read upright.
 */
export function LayoutPlan(props: PlanProps) {
  const drawingRef = useRef<SVGGElement>(null);
  const drag = usePlanDrag(drawingRef, props);
  const ids = { hatch: useId(), lift: useId() };
  const frame = useMemo(() => frameOf(props.scanned), [props.scanned]);
  const nodes = useMemo(() => drawOrder(drawnNodes(props.shown), frame.floor), [props.shown, frame.floor]);
  const heldId = drag.draggingId ?? props.activeId;
  const draw: DrawContext = { props, drag, frame, hatch: `url(#${ids.hatch})`, lift: `url(#${ids.lift})` };
  return (
    <svg viewBox={viewBoxAttribute(frame.box)} onPointerDown={(event) => closeStaffUnlessOnIt(props.staff, event.target)} className="size-full touch-none select-none" role="group" aria-label="Your shop from above. Drag a piece to move it, or focus one and use the arrow keys.">
      <PlanDefs ids={ids} frame={frame} />
      <g ref={drawingRef} transform={`rotate(${frame.turn.toFixed(3)})`}>
        {nodes.filter((node) => node.kind === "floor").map((node) => <Backdrop key={node.id} node={node} />)}
        {props.staff && <StaffPlanAreas handles={props.staff} svgRef={drawingRef} />}
        <Ghosts shown={props.shown} scanned={props.scanned} movedIds={props.movedIds} />
        {nodes.filter((node) => node.kind !== "floor").map((node) => <PlanNode key={node.id} node={node} draw={draw} />)}
        {props.cleared.map((finding) => <DimensionLine key={`cleared-${finding.id}`} finding={finding} tone="cleared" />)}
        {props.problems.map((finding) => <DimensionLine key={finding.id} finding={finding} tone="problem" />)}
      </g>
      {props.staff && <StaffPlanLabels areas={props.staff.areas} turnDegrees={frame.turn} size={frame.text} />}
      <MeasurementLabels problems={props.problems} frame={frame} />
      <NameTag node={props.shown.nodes.find((node) => node.id === heldId)} frame={frame} />
    </svg>
  );
}

type DrawContext = { props: PlanProps; drag: DragBinding; frame: PlanFrame; hatch: string; lift: string };

function PlanDefs({ ids, frame }: { ids: { hatch: string; lift: string }; frame: PlanFrame }) {
  return (
    <defs>
      <pattern id={ids.hatch} width={0.08} height={0.08} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
        <line x1="0" y1="0" x2="0" y2="0.08" stroke="var(--color-ink)" strokeWidth={0.015} strokeOpacity={0.5} />
      </pattern>
      <filter id={ids.lift} x="-50%" y="-50%" width="200%" height="200%">
        <feDropShadow dx={0} dy={0} stdDeviation={frame.text * 0.3} floodColor="var(--color-ink)" floodOpacity={0.35} />
      </filter>
    </defs>
  );
}

function PlanNode({ node, draw }: { node: SceneNode; draw: DrawContext }) {
  const { props, drag, frame } = draw;
  const role = planRole(node, frame.floor);
  if (role === "backdrop") return <Backdrop node={node} />;
  if (role === "fixed") return <FixedPiece node={node} hatch={draw.hatch} turnDegrees={frame.turn} onTap={props.onFixedTap} />;
  if (role === "riding") return <RidingPiece node={node} scene={props.shown} state={props} />;
  return <MovablePiece node={node} state={props} held={drag.draggingId === node.id} lift={draw.lift} drag={drag} onKey={(event) => props.onKey(node.id, event, frame.turn)} />;
}

/** A tap anywhere on the plan but a staff area closes the open one, as a tap off it does in 3D. */
function closeStaffUnlessOnIt(staff: StaffHandles | null, target: EventTarget) {
  if (!staff || staff.chosen === null || staff.chosen === "all") return;
  if (!(target instanceof Element) || !target.closest(`[${STAFF_AREA_ATTRIBUTE}]`)) staff.onChoose(null);
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

function FixedPiece({ node, hatch, turnDegrees, onTap }: { node: SceneNode; hatch: string; turnDegrees: number; onTap: (nodeId: string) => void }) {
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
      {lockFits && <LockGlyph x={x} y={-y} turnDegrees={turnDegrees} />}
    </g>
  );
}

/** The padlock, upright whatever way the piece or the plan faces, in a disc so it reads over the hatch. */
function LockGlyph({ x, y, turnDegrees }: { x: number; y: number; turnDegrees: number }) {
  const half = LOCK_SIZE_METERS / 2;
  return (
    <g aria-hidden transform={`translate(${x.toFixed(4)} ${y.toFixed(4)}) rotate(${(-turnDegrees).toFixed(3)})`}>
      <circle r={half * 1.25} fill="var(--color-ink)" />
      <Lock x={-half * 0.7} y={-half * 0.7} size={LOCK_SIZE_METERS * 0.7} weight="bold" color="var(--color-paper)" />
    </g>
  );
}

type DragBinding = ReturnType<typeof usePlanDrag>;

type Tone = { fill: string; stroke: string; width: number };

function pieceTone(nodeId: string, state: PieceState): Tone {
  if (state.blockedIds.has(nodeId)) return { fill: "var(--color-problem)", stroke: "var(--color-problem)", width: 2.5 };
  if (state.activeId === nodeId || state.pointedIds.has(nodeId)) return { fill: "var(--color-accent)", stroke: "var(--color-accent)", width: 2.5 };
  return { fill: "var(--color-ink)", stroke: "var(--color-ink)", width: 1.25 };
}

/**
 * Something resting on another piece, a laptop on a table or a drawer in a case.
 * It is drawn faintly in the tone of what holds it and lets the pointer through,
 * so grabbing the table grabs the table; whatever sits on top travels with it.
 */
function RidingPiece({ node, scene, state }: { node: SceneNode; scene: SceneGraph; state: PieceState }) {
  const { width, depth, transform } = placement(node);
  const toneOf = state.pointedIds.has(node.id) ? node.id : (supportOf(scene, node)?.id ?? node.id);
  const tone = pieceTone(toneOf, state);
  return (
    <rect
      x={-width / 2} y={-depth / 2} width={width} height={depth} style={{ transform }} aria-hidden
      className="pointer-events-none transition-transform duration-200 ease-[var(--ease-settle)] motion-reduce:transition-none"
      fill={tone.fill} fillOpacity={0.14} stroke={tone.stroke} strokeOpacity={0.45} strokeWidth={1} vectorEffect="non-scaling-stroke"
    />
  );
}

function MovablePiece({ node, state, held, lift, drag, onKey }: { node: SceneNode; state: PieceState; held: boolean; lift: string; drag: DragBinding; onKey: (event: KeyboardEvent) => void }) {
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
      className={`cursor-grab outline-none focus-visible:[&_rect:nth-child(2)]:stroke-accent ${held ? "cursor-grabbing" : "transition-transform duration-200 ease-[var(--ease-settle)] motion-reduce:transition-none"}`}
      onPointerDown={(event: PointerEvent<SVGGElement>) => drag.grab(node.id, event)}
      onPointerMove={drag.move}
      onPointerUp={drag.release}
      onPointerCancel={drag.release}
      onKeyDown={onKey}
    >
      <g className="plan-lift" data-held={held || undefined} filter={held ? lift : undefined}>
        <rect x={-grabWidth / 2} y={-grabDepth / 2} width={grabWidth} height={grabDepth} fill="transparent" />
        <rect
          x={-width / 2} y={-depth / 2} width={width} height={depth}
          fill={tone.fill} fillOpacity={held || moved ? 0.32 : 0.18}
          stroke={tone.stroke} strokeWidth={tone.width} strokeDasharray={moved ? undefined : "4 3"}
          vectorEffect="non-scaling-stroke"
        />
      </g>
    </g>
  );
}

/** Where each moved piece was scanned, drawn faintly so the owner can see what moved and how far. */
function Ghosts({ shown, scanned, movedIds }: { shown: SceneGraph; scanned: SceneGraph; movedIds: Set<string> }) {
  const ghosts = useMemo(() => {
    const now = new Map(shown.nodes.map((node) => [node.id, node]));
    return scanned.nodes.filter((node) => {
      const current = now.get(node.id);
      return movedIds.has(node.id) && current !== undefined && hasMoved(node, current);
    });
  }, [shown, scanned, movedIds]);
  return (
    <g aria-hidden className="pointer-events-none">
      {ghosts.map((node) => {
        const { width, depth, transform } = placement(node);
        return <rect key={node.id} x={-width / 2} y={-depth / 2} width={width} height={depth} style={{ transform }} fill="none" stroke="var(--color-ink-faint)" strokeWidth={1} strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />;
      })}
    </g>
  );
}

/** The held or chosen piece's name, just above it on screen and kept inside the drawing. */
function NameTag({ node, frame }: { node: SceneNode | undefined; frame: PlanFrame }) {
  if (!node || node.kind !== "object") return null;
  const centre = turnedPoint(footprint(node), frame.turn);
  const above = centre.y - turnedHalfExtent(node, frame.turn).y - frame.text * 0.45;
  const y = Math.max(above, frame.box.minY + frame.text * 1.1);
  return (
    <text x={centre.x} y={y} fontSize={frame.text} textAnchor="middle" className="plan-label pointer-events-none" fill="var(--color-ink)" strokeWidth={frame.text * 0.3} aria-hidden>
      {displayName(node)}
    </text>
  );
}

const DIMENSION_TONE = {
  problem: { stroke: "var(--color-problem)", dash: undefined, opacity: 1 },
  cleared: { stroke: "var(--color-pass)", dash: "6 4", opacity: 0.9 },
};

/** A measurement line drawn where it was taken: red while it misses, dashed green once the layout clears it. */
function DimensionLine({ finding, tone }: { finding: Finding; tone: keyof typeof DIMENSION_TONE }) {
  const annotation = finding.locus?.annotation;
  if (!annotation || annotation.points.length < 2) return null;
  const style = DIMENSION_TONE[tone];
  const line = annotation.points.map((point) => `${point.x.toFixed(4)},${(-point.y).toFixed(4)}`).join(" ");
  return (
    <polyline aria-hidden opacity={style.opacity} className="pointer-events-none" points={line} fill="none" stroke={style.stroke} strokeWidth={3} strokeDasharray={style.dash} strokeLinecap="round" vectorEffect="non-scaling-stroke" />
  );
}

/** The stretch of a measured line at its middle, where its label goes. */
function middleStretch(points: Vec3[]): [Vec3, Vec3] {
  const after = Math.max(1, Math.floor(points.length / 2));
  return [points[after - 1], points[after]];
}

function measurementLabels(problems: Finding[], frame: PlanFrame): PlanLabel[] {
  const centreX = frame.box.minX + frame.box.width / 2;
  return problems.flatMap((finding) => {
    const annotation = finding.locus?.annotation;
    if (!annotation?.label || annotation.points.length < 2) return [];
    const [a, b] = middleStretch(annotation.points).map((point) => turnedPoint(point, frame.turn));
    const at = labelBesideLine(a, b, annotation.label, frame.text, centreX);
    return [{ key: finding.id, text: annotation.label, ...at }];
  });
}

/** The problems' measurements, upright, one per spot, and slid apart where they would overlap. */
function MeasurementLabels({ problems, frame }: { problems: Finding[]; frame: PlanFrame }) {
  const labels = useMemo(() => placeLabels(measurementLabels(problems, frame), frame.text), [problems, frame]);
  return (
    <g aria-hidden className="pointer-events-none">
      {labels.map((label) => (
        <text key={label.key} x={label.x} y={label.y} fontSize={frame.text} textAnchor="middle" className="measurement plan-label" fill="var(--color-problem)" strokeWidth={frame.text * 0.25}>
          {label.text}
        </text>
      ))}
    </g>
  );
}
