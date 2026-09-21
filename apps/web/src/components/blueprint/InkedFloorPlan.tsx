"use client";

import { useEffect, useMemo, useRef } from "react";
import { FloorPlan } from "@/components/FloorPlan";
import { planAnnotations, type PlanAnnotations } from "@/lib/plan-annotations";
import { dimensionAbove, dimensionLeft, leaderNoteAt } from "@/lib/schematic-brush/annotations";
import { SchematicBrush } from "@/lib/schematic-brush/brush";
import type { Point } from "@/lib/schematic-brush/geometry";
import { DRAFTING_UNIT_PX } from "@/lib/schematic-brush/sheet";
import type { SceneGraph } from "@/types/contracts";
import { clearCanvas, createTextMeasurer, fitCanvases, readPalette, startInkLoop } from "./inkCanvas";

const PEN_SPEED_MULTIPLIER = 1.4;
const DIMENSION_OFFSET_PX = 34;
const SHELF_OFFSET_PX = 30;
const NOTE_SPACING_PX = 34;
const NOTES_MIN_WIDTH_PX = 560;
const REDRAW_DELAY_MS = 200;

type ToFrame = (x: number, y: number) => Point;

function planToFrame(svg: SVGSVGElement, frame: HTMLElement): ToFrame | null {
  const matrix = svg.getScreenCTM();
  if (!matrix) return null;
  const origin = frame.getBoundingClientRect();
  return (x, y) => {
    const point = new DOMPoint(x, -y).matrixTransform(matrix);
    return [point.x - origin.left, point.y - origin.top];
  };
}

function shelfRows(targets: Point[], top: number, bottom: number) {
  const order = targets.map((target, index) => ({ target, index })).sort((a, b) => a.target[1] - b.target[1]);
  const rows = new Array<number>(targets.length);
  let next = top;
  for (const { target, index } of order) {
    rows[index] = Math.min(bottom, Math.max(next, target[1]));
    next = rows[index] + NOTE_SPACING_PX;
  }
  return rows;
}

function draftPlan(brush: SchematicBrush, annotations: PlanAnnotations, toFrame: ToFrame, frameWidth: number) {
  const { minX, minY, width, height } = annotations.bounds;
  const topLeft = toFrame(minX, minY + height);
  const topRight = toFrame(minX + width, minY + height);
  const bottomLeft = toFrame(minX, minY);
  brush.stampWith((d) => dimensionAbove(d, topLeft, topRight, topLeft[1] - DIMENSION_OFFSET_PX, annotations.widthLabel));
  brush.stampWith((d) => dimensionLeft(d, topLeft, bottomLeft, topLeft[0] - DIMENSION_OFFSET_PX, annotations.depthLabel), 500);
  if (frameWidth < NOTES_MIN_WIDTH_PX) return;
  const targets = annotations.notes.map((note) => toFrame(note.x, note.y));
  const rows = shelfRows(targets, topRight[1], bottomLeft[1]);
  targets.forEach((target, index) => {
    const shelf: Point = [topRight[0] + SHELF_OFFSET_PX, rows[index]];
    brush.stampWith((d) => leaderNoteAt(d, target, shelf, annotations.notes[index].text), 1000 + index * 350);
  });
}

export function InkedFloorPlan({ scene, className = "" }: { scene: SceneGraph; className?: string }) {
  const frameRef = useRef<HTMLDivElement>(null);
  const paintRef = useRef<HTMLCanvasElement>(null);
  const liveRef = useRef<HTMLCanvasElement>(null);
  const annotations = useMemo(() => planAnnotations(scene), [scene]);

  useEffect(() => {
    const [frame, paint, live] = [frameRef.current, paintRef.current, liveRef.current];
    const svg = frame?.querySelector<SVGSVGElement>("svg[data-floor-plan]");
    if (!frame || !paint || !live || !svg || !annotations) return;
    const palette = readPalette(frame, "--color-paper");
    const brush = new SchematicBrush({ unit: DRAFTING_UNIT_PX, measureText: createTextMeasurer(palette.fontFamily), now: () => performance.now(), speed: PEN_SPEED_MULTIPLIER });
    const stopLoop = startInkLoop(brush, paint, live, palette);
    let redrawTimer: number | undefined;
    let disposed = false;

    const redraw = () => {
      const toFrame = planToFrame(svg, frame);
      if (disposed || !toFrame) return;
      brush.clear();
      clearCanvas(paint);
      clearCanvas(live);
      fitCanvases(frame, paint, live);
      draftPlan(brush, annotations, toFrame, frame.clientWidth);
    };
    const observer = new ResizeObserver(() => {
      window.clearTimeout(redrawTimer);
      redrawTimer = window.setTimeout(redraw, REDRAW_DELAY_MS);
    });
    document.fonts.ready.then(() => {
      redraw();
      observer.observe(frame);
    });

    return () => {
      disposed = true;
      window.clearTimeout(redrawTimer);
      observer.disconnect();
      stopLoop();
    };
  }, [annotations]);

  return (
    <div ref={frameRef} className={`relative ${className}`}>
      <div className="absolute top-14 right-6 bottom-6 left-14 sm:right-64 sm:left-20">
        <FloorPlan scene={scene} className="size-full" />
      </div>
      <canvas ref={paintRef} aria-hidden className="pointer-events-none absolute inset-0 size-full" />
      <canvas ref={liveRef} aria-hidden className="pointer-events-none absolute inset-0 size-full" />
      {annotations && (
        <p className="sr-only">
          The shop measures {annotations.widthLabel} wide by {annotations.depthLabel} deep.
          {annotations.notes.map((note) => ` ${note.text}.`).join("")}
        </p>
      )}
    </div>
  );
}
