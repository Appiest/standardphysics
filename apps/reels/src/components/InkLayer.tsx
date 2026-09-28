import { useLayoutEffect, useRef } from "react";
import { renderStamp, type InkPalette } from "../../../web/src/lib/schematic-brush/render";
import { useFontsLoaded } from "../fonts";
import type { Stamp } from "../lib/ink";

type InkLayerProps = {
  stamps: readonly Stamp[];
  timeMs: number;
  width: number;
  height: number;
  ink?: string;
  /** Draws the sheet this many times larger, so pen weights and labels read at phone size. Stamps are composed in the smaller space. */
  zoom?: number;
  className?: string;
};

type SettledCache = { canvas: OffscreenCanvas; drawnThrough: number; stamps: readonly Stamp[] };

function paletteFor(ink: string): InkPalette {
  return { ink, paper: "transparent", fontFamily: `"Libre Franklin", "Karla", sans-serif` };
}

/** Stamps that have finished drawing never change again, so they are painted once and reused while time moves forward. */
function settle(cache: SettledCache, timeMs: number, palette: InkPalette, zoom: number) {
  const context = cache.canvas.getContext("2d") as unknown as CanvasRenderingContext2D;
  context.setTransform(zoom, 0, 0, zoom, 0, 0);
  if (timeMs < cache.drawnThrough) {
    context.clearRect(0, 0, cache.canvas.width, cache.canvas.height);
    cache.drawnThrough = -Infinity;
  }
  for (const stamp of cache.stamps) {
    if (stamp.endsAt <= timeMs && stamp.endsAt > cache.drawnThrough) renderStamp(context, stamp, Infinity, palette);
  }
  cache.drawnThrough = timeMs;
}

function paintFrame(context: CanvasRenderingContext2D, cache: SettledCache, timeMs: number, palette: InkPalette, zoom: number) {
  context.setTransform(1, 0, 0, 1, 0, 0);
  context.clearRect(0, 0, context.canvas.width, context.canvas.height);
  context.drawImage(cache.canvas, 0, 0);
  context.setTransform(zoom, 0, 0, zoom, 0, 0);
  for (const stamp of cache.stamps) {
    if (stamp.startsAt <= timeMs && stamp.endsAt > timeMs) renderStamp(context, stamp, timeMs, palette);
  }
}

export function InkLayer({ stamps, timeMs, width, height, ink = "#0d0d0c", zoom = 1, className }: InkLayerProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const cacheRef = useRef<SettledCache | null>(null);
  const fontsLoaded = useFontsLoaded();

  useLayoutEffect(() => {
    const context = canvasRef.current?.getContext("2d");
    if (!context || !fontsLoaded) return;
    if (cacheRef.current?.stamps !== stamps) cacheRef.current = { canvas: new OffscreenCanvas(width, height), drawnThrough: -Infinity, stamps };
    const palette = paletteFor(ink);
    settle(cacheRef.current, timeMs, palette, zoom);
    paintFrame(context, cacheRef.current, timeMs, palette, zoom);
  }, [stamps, timeMs, width, height, ink, zoom, fontsLoaded]);

  return <canvas ref={canvasRef} width={width} height={height} className={className} />;
}
