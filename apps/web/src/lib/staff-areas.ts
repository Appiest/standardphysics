import type { StaffArea } from "@/types/contracts";

/** The staff-only areas as a view draws them, and what the owner can do to them there. */
export type StaffHandles = {
  areas: StaffArea[];
  editable: boolean;
  /** The area whose corners show and which drags; "all" keeps every area open. */
  chosen: number | "all" | null;
  onChoose: (index: number | null) => void;
  onGrab: () => void;
  onMove: (index: number, dx: number, dy: number) => void;
  onResize: (index: number, corner: Corner, to: { x: number; y: number }) => void;
  onDrop: () => void;
};

/** Which corner a handle sits on, as signs along the area's own width and depth. */
export type Corner = { alongSign: 1 | -1; acrossSign: 1 | -1 };

export const CORNERS: Corner[] = [
  { alongSign: 1, acrossSign: 1 },
  { alongSign: -1, acrossSign: 1 },
  { alongSign: -1, acrossSign: -1 },
  { alongSign: 1, acrossSign: -1 },
];

const SMALLEST_METERS = 0.5;
const NEW_AREA = { width: 3, depth: 2 };

function axes(area: StaffArea) {
  const turn = (area.rotation_z_degrees * Math.PI) / 180;
  return { along: { x: Math.cos(turn), y: Math.sin(turn) }, across: { x: -Math.sin(turn), y: Math.cos(turn) } };
}

/** A point given in the area's own frame, in room coordinates. */
function toRoom(area: StaffArea, along: number, across: number) {
  const axis = axes(area);
  return {
    x: area.centre.x + axis.along.x * along + axis.across.x * across,
    y: area.centre.y + axis.along.y * along + axis.across.y * across,
  };
}

export function cornerPoint(area: StaffArea, corner: Corner) {
  return toRoom(area, (corner.alongSign * area.width) / 2, (corner.acrossSign * area.depth) / 2);
}

export function outline(area: StaffArea) {
  return CORNERS.map((corner) => cornerPoint(area, corner));
}

export function moveArea(area: StaffArea, dx: number, dy: number): StaffArea {
  return { ...area, centre: { ...area.centre, x: area.centre.x + dx, y: area.centre.y + dy } };
}

/** Drag one corner to a floor point while the opposite corner stays where it is. */
export function resizeArea(area: StaffArea, corner: Corner, to: { x: number; y: number }): StaffArea {
  const fixed = cornerPoint(area, { alongSign: -corner.alongSign as 1 | -1, acrossSign: -corner.acrossSign as 1 | -1 });
  const axis = axes(area);
  const offset = { x: to.x - fixed.x, y: to.y - fixed.y };
  const width = Math.max(SMALLEST_METERS, corner.alongSign * (offset.x * axis.along.x + offset.y * axis.along.y));
  const depth = Math.max(SMALLEST_METERS, corner.acrossSign * (offset.x * axis.across.x + offset.y * axis.across.y));
  const halfAlong = (corner.alongSign * width) / 2;
  const halfAcross = (corner.acrossSign * depth) / 2;
  return {
    ...area,
    width,
    depth,
    centre: {
      ...area.centre,
      x: fixed.x + axis.along.x * halfAlong + axis.across.x * halfAcross,
      y: fixed.y + axis.along.y * halfAlong + axis.across.y * halfAcross,
    },
  };
}

export function newArea(centre: { x: number; y: number }): StaffArea {
  return { name: "Staff area", centre: { x: centre.x, y: centre.y, z: 0 }, ...NEW_AREA, rotation_z_degrees: 0 };
}

/** Whether an area shows its corners and drags, rather than waiting for a tap. */
export function isOpen(handles: StaffHandles, index: number) {
  return handles.editable && (handles.chosen === "all" || handles.chosen === index);
}
