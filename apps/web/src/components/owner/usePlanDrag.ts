"use client";

import { type PointerEvent as ReactPointerEvent, type RefObject, useCallback, useEffect, useRef, useState } from "react";

export type PlanDragHandlers = {
  onGrab: (nodeId: string) => void;
  onDrag: (nodeId: string, dx: number, dy: number) => void;
  onDrop: (nodeId: string) => void;
};

type Grip = { nodeId: string; pointerId: number; last: { x: number; y: number } };

/** Where a pointer lands in the plan's own units: metres, with y flipped because the plan draws north up. */
function roomPoint(svg: SVGSVGElement, event: { clientX: number; clientY: number }) {
  const matrix = svg.getScreenCTM()?.inverse();
  if (!matrix) return null;
  const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix);
  return { x: point.x, y: -point.y };
}

/**
 * Dragging a piece on the plan with a mouse, a pen or a finger. Moves are
 * summed and handed over once per frame, so a fast drag costs one layout per
 * frame rather than one per pointer event.
 */
export function usePlanDrag(svgRef: RefObject<SVGSVGElement | null>, handlers: PlanDragHandlers) {
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
    const at = svgRef.current && roomPoint(svgRef.current, event);
    if (!at || event.button > 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    grip.current = { nodeId, pointerId: event.pointerId, last: at };
    pending.current = { dx: 0, dy: 0 };
    setDraggingId(nodeId);
    latest.current.onGrab(nodeId);
  }, [svgRef]);

  const move = useCallback((event: ReactPointerEvent<SVGElement>) => {
    const held = grip.current;
    const at = held && held.pointerId === event.pointerId && svgRef.current && roomPoint(svgRef.current, event);
    if (!held || !at) return;
    pending.current = { dx: pending.current.dx + at.x - held.last.x, dy: pending.current.dy + at.y - held.last.y };
    held.last = at;
    if (frame.current === null) frame.current = requestAnimationFrame(flush);
  }, [svgRef, flush]);

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
