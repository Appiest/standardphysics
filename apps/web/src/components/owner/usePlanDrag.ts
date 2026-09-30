"use client";

import { type PointerEvent as ReactPointerEvent, type RefObject, useCallback, useEffect, useRef, useState } from "react";
import { roomPointFrom } from "@/lib/plan-view";

export type PlanDragHandlers = {
  onGrab: (nodeId: string) => void;
  onDrag: (nodeId: string, dx: number, dy: number) => void;
  onDrop: (nodeId: string) => void;
};

type Grip = { nodeId: string; pointerId: number; last: { x: number; y: number } };

/**
 * Where a pointer lands in room metres. `drawing` is the group the room is
 * drawn in, inside the plan's turn, so its screen matrix undoes the turn too.
 */
export function roomPoint(drawing: SVGGraphicsElement, event: { clientX: number; clientY: number }) {
  const inverse = drawing.getScreenCTM()?.inverse();
  return inverse ? roomPointFrom(inverse, event) : null;
}

/**
 * Dragging a piece on the plan with a mouse, a pen or a finger. Moves are
 * summed and handed over once per frame, so a fast drag costs one layout per
 * frame rather than one per pointer event.
 */
export function usePlanDrag(drawingRef: RefObject<SVGGraphicsElement | null>, handlers: PlanDragHandlers) {
  const grip = useRef<Grip | null>(null);
  const pending = useRef({ dx: 0, dy: 0 });
  const frame = useRef<number | null>(null);
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const latest = useRef(handlers);
  useEffect(() => { latest.current = handlers; }, [handlers]);
  useEffect(() => () => { if (frame.current !== null) cancelAnimationFrame(frame.current); }, []);

  const flush = useCallback(() => {
    frame.current = null;
    const { dx, dy } = pending.current;
    if (!grip.current || (dx === 0 && dy === 0)) return;
    pending.current = { dx: 0, dy: 0 };
    latest.current.onDrag(grip.current.nodeId, dx, dy);
  }, []);

  const grab = useCallback((nodeId: string, event: ReactPointerEvent<SVGElement>) => {
    const at = drawingRef.current && roomPoint(drawingRef.current, event);
    if (!at || event.button > 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    grip.current = { nodeId, pointerId: event.pointerId, last: at };
    pending.current = { dx: 0, dy: 0 };
    setDraggingId(nodeId);
    latest.current.onGrab(nodeId);
  }, [drawingRef]);

  const move = useCallback((event: ReactPointerEvent<SVGElement>) => {
    const held = grip.current;
    const at = held && held.pointerId === event.pointerId && drawingRef.current && roomPoint(drawingRef.current, event);
    if (!held || !at) return;
    pending.current = { dx: pending.current.dx + at.x - held.last.x, dy: pending.current.dy + at.y - held.last.y };
    held.last = at;
    if (frame.current === null) frame.current = requestAnimationFrame(flush);
  }, [drawingRef, flush]);

  const release = useCallback((event: ReactPointerEvent<SVGElement>) => {
    const held = grip.current;
    if (!held || held.pointerId !== event.pointerId) return;
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    flush();
    grip.current = null;
    setDraggingId(null);
    latest.current.onDrop(held.nodeId);
  }, [flush]);

  return { draggingId, grab, move, release };
}
