"use client";

import { type PointerEvent, type RefObject, useId, useRef } from "react";
import { CORNERS, type Corner, cornerPoint, isOpen, outline, type StaffHandles } from "@/lib/staff-areas";
import type { StaffArea } from "@/types/contracts";
import { roomPoint } from "./usePlanDrag";

const HANDLE_RADIUS_METERS = 0.16;
const HANDLE_GRAB_METERS = 0.35;
const LABEL_SIZE_METERS = 0.3;

type FloorPoint = { x: number; y: number };
type Drag = (from: FloorPoint, to: FloorPoint) => void;
type SvgRef = RefObject<SVGSVGElement | null>;

/** Marks what belongs to a staff area, so a tap anywhere else on the plan closes the open one. */
export const STAFF_AREA_ATTRIBUTE = "data-staff-area";

/** Pointer handlers that report plan points in room coordinates for one drag. */
function usePlanPointerDrag(svgRef: SvgRef, handles: StaffHandles, onDrag: Drag) {
  const last = useRef<FloorPoint | null>(null);
  const at = (event: PointerEvent<SVGElement>) => svgRef.current && roomPoint(svgRef.current, event);
  return {
    onPointerDown(event: PointerEvent<SVGElement>) {
      if (event.button > 0) return;
      event.preventDefault();
      event.currentTarget.setPointerCapture(event.pointerId);
      last.current = at(event);
      handles.onGrab();
    },
    onPointerMove(event: PointerEvent<SVGElement>) {
      const point = last.current && at(event);
      if (!last.current || !point) return;
      onDrag(last.current, point);
      last.current = point;
    },
    onPointerUp(event: PointerEvent<SVGElement>) {
      if (!last.current) return;
      event.currentTarget.releasePointerCapture(event.pointerId);
      last.current = null;
      handles.onDrop();
    },
  };
}

function flipped(points: FloorPoint[]) {
  return points.map(({ x, y }) => `${x.toFixed(4)},${(-y).toFixed(4)}`).join(" ");
}

function PlanCorner({ area, corner, index, handles, svgRef }: { area: StaffArea; corner: Corner; index: number; handles: StaffHandles; svgRef: SvgRef }) {
  const drag = usePlanPointerDrag(svgRef, handles, (_from, to) => handles.onResize(index, corner, to));
  const { x, y } = cornerPoint(area, corner);
  return (
    <g className="cursor-move" {...drag}>
      <circle cx={x} cy={-y} r={HANDLE_GRAB_METERS} fill="transparent" />
      <circle cx={x} cy={-y} r={HANDLE_RADIUS_METERS} fill="var(--color-ink)" stroke="var(--color-paper)" strokeWidth={2} vectorEffect="non-scaling-stroke" />
    </g>
  );
}

function PlanArea({ area, index, handles, hatch, svgRef }: { area: StaffArea; index: number; handles: StaffHandles; hatch: string; svgRef: SvgRef }) {
  const open = isOpen(handles, index);
  const drag = usePlanPointerDrag(svgRef, handles, (from, to) => handles.onMove(index, to.x - from.x, to.y - from.y));
  const tap = handles.editable ? { onClick: () => handles.onChoose(index), className: "cursor-pointer" } : {};
  const points = flipped(outline(area));
  return (
    <g {...{ [STAFF_AREA_ATTRIBUTE]: "" }}>
      <g {...(open ? { ...drag, className: "cursor-grab" } : tap)}>
        <title>Staff only</title>
        <polygon points={points} fill={hatch} />
        <polygon points={points} fill="none" stroke="var(--color-ink)" strokeWidth={open ? 2.5 : 1.5} strokeDasharray={open ? undefined : "6 4"} vectorEffect="non-scaling-stroke" />
      </g>
      {open && CORNERS.map((corner) => (
        <PlanCorner key={`${corner.alongSign}${corner.acrossSign}`} area={area} corner={corner} index={index} handles={handles} svgRef={svgRef} />
      ))}
    </g>
  );
}

/** Drawn above the furniture, so a counter standing in the area can't hide what the area is. */
export function StaffPlanLabels({ areas }: { areas: StaffArea[] }) {
  return (
    <g className="pointer-events-none" aria-hidden>
      {areas.map((area, index) => (
        <text key={index} x={area.centre.x} y={-area.centre.y} dy={LABEL_SIZE_METERS * 0.35} fontSize={LABEL_SIZE_METERS} textAnchor="middle" fontWeight={600} fill="var(--color-ink)" stroke="var(--color-paper)" strokeWidth={LABEL_SIZE_METERS * 0.25} paintOrder="stroke">
          Staff only
        </text>
      ))}
    </g>
  );
}

/** The staff-only floor on the plan, the same areas the 3D view draws, opened with a tap to move or resize. */
export function StaffPlanAreas({ handles, svgRef }: { handles: StaffHandles; svgRef: SvgRef }) {
  const hatchId = useId();
  return (
    <g>
      <defs>
        <pattern id={hatchId} width={0.2} height={0.2} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width={0.08} height={0.2} fill="var(--color-ink)" fillOpacity={0.22} />
        </pattern>
      </defs>
      {handles.areas.map((area, index) => (
        <PlanArea key={index} area={area} index={index} handles={handles} hatch={`url(#${hatchId})`} svgRef={svgRef} />
      ))}
    </g>
  );
}
