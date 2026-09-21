"use client";

import { useCallback, useEffect, useRef, useState, type PointerEvent } from "react";
import { SchematicBrush } from "@/lib/schematic-brush/brush";
import type { Box } from "@/lib/schematic-brush/geometry";
import type { InkPalette } from "@/lib/schematic-brush/render";
import {
  DEMO_ROUTE_DURATION_MS,
  demoRoute,
  DRAFTING_UNIT_PX,
  zoneAt,
  zoneGridFor,
  type ZoneGrid,
} from "@/lib/schematic-brush/sheet";
import { clearCanvas, context2d, createTextMeasurer, fitCanvases, readPalette, startInkLoop } from "./inkCanvas";

function avoidedAreas(surface: HTMLElement): Box[] {
  const origin = surface.getBoundingClientRect();
  return Array.from(surface.querySelectorAll("[data-ink-avoid]"), (element) => {
    const rect = element.getBoundingClientRect();
    return [rect.left - origin.left - 8, rect.top - origin.top - 8, rect.right - origin.left, rect.bottom - origin.top] as const;
  });
}

function sameZoneGrid(a: ZoneGrid, b: ZoneGrid) {
  return a.columns === b.columns && a.rows === b.rows;
}

export function useSchematicBrush() {
  const surfaceRef = useRef<HTMLDivElement>(null);
  const paintRef = useRef<HTMLCanvasElement>(null);
  const liveRef = useRef<HTMLCanvasElement>(null);
  const brushRef = useRef<SchematicBrush | null>(null);
  const paletteRef = useRef<InkPalette | null>(null);
  const [zoneGrid, setZoneGrid] = useState<ZoneGrid>({ columns: 8, rows: 6 });
  const [penZone, setPenZone] = useState<string | null>(null);
  const [hasDrawn, setHasDrawn] = useState(false);

  const playDemo = useCallback(() => {
    const surface = surfaceRef.current;
    if (!surface) return;
    brushRef.current?.draftRoute(demoRoute(surface.clientWidth, surface.clientHeight), DEMO_ROUTE_DURATION_MS);
  }, []);

  useEffect(() => {
    const surface = surfaceRef.current;
    const paint = paintRef.current;
    const live = liveRef.current;
    if (!surface || !paint || !live) return;
    const palette = readPalette(surface);
    paletteRef.current = palette;
    const brush = new SchematicBrush({ unit: DRAFTING_UNIT_PX, measureText: createTextMeasurer(palette.fontFamily), now: () => performance.now() });
    brushRef.current = brush;
    const resize = () => {
      fitCanvases(surface, paint, live);
      brush.setKeepOuts(avoidedAreas(surface), null);
      const next = zoneGridFor(surface.clientWidth, surface.clientHeight);
      setZoneGrid((current) => (sameZoneGrid(current, next) ? current : next));
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(surface);

    const stopLoop = startInkLoop(brush, paint, live, palette);
    let disposed = false;
    document.fonts.ready.then(() => {
      if (!disposed) playDemo();
    });

    return () => {
      disposed = true;
      stopLoop();
      observer.disconnect();
      brush.clear();
    };
  }, [playDemo]);

  const localPoint = (event: PointerEvent<HTMLCanvasElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    return { x: event.clientX - bounds.left, y: event.clientY - bounds.top, width: bounds.width, height: bounds.height };
  };

  const onPointerDown = (event: PointerEvent<HTMLCanvasElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    const { x, y } = localPoint(event);
    brushRef.current?.pointerDown(x, y);
    setHasDrawn(true);
  };

  const onPointerMove = (event: PointerEvent<HTMLCanvasElement>) => {
    const { x, y, width, height } = localPoint(event);
    brushRef.current?.pointerMove(x, y);
    setPenZone(zoneAt(x, y, width, height));
  };

  const onPointerUp = () => brushRef.current?.pointerUp();

  const clearSheet = useCallback(() => {
    brushRef.current?.clear();
    if (paintRef.current) clearCanvas(paintRef.current);
    if (liveRef.current) clearCanvas(liveRef.current);
  }, []);

  const replayDemo = useCallback(() => {
    clearSheet();
    playDemo();
  }, [clearSheet, playDemo]);

  const saveImage = useCallback(() => {
    const paint = paintRef.current;
    const palette = paletteRef.current;
    if (!paint || !palette) return;
    const exported = document.createElement("canvas");
    exported.width = paint.width;
    exported.height = paint.height;
    const context = context2d(exported);
    context.fillStyle = palette.paper;
    context.fillRect(0, 0, exported.width, exported.height);
    context.drawImage(paint, 0, 0);
    const link = document.createElement("a");
    link.download = "standard-physics-sheet.png";
    link.href = exported.toDataURL("image/png");
    link.click();
  }, []);

  return {
    surfaceRef,
    paintRef,
    liveRef,
    zoneGrid,
    penZone,
    hasDrawn,
    canvasHandlers: { onPointerDown, onPointerMove, onPointerUp, onPointerCancel: onPointerUp, onPointerLeave: () => setPenZone(null) },
    clearSheet,
    replayDemo,
    saveImage,
  };
}
