import type { SchematicBrush } from "@/lib/schematic-brush/brush";
import { renderStamp, type InkPalette } from "@/lib/schematic-brush/render";

const MAX_CANVAS_PIXELS_TALL = 12000;

export function readPalette(element: HTMLElement, surfaceToken = "--color-sheet"): InkPalette {
  const styles = getComputedStyle(element);
  return {
    ink: styles.getPropertyValue("--color-ink").trim(),
    paper: styles.getPropertyValue(surfaceToken).trim(),
    fontFamily: styles.fontFamily,
  };
}

export function createTextMeasurer(fontFamily: string) {
  const context = document.createElement("canvas").getContext("2d");
  return (text: string, size: number) => {
    if (!context) return text.length * size * 0.55;
    context.font = `500 ${size}px ${fontFamily}`;
    return context.measureText(text).width;
  };
}

export function context2d(canvas: HTMLCanvasElement) {
  const context = canvas.getContext("2d");
  if (!context) throw new Error("This browser can't draw on a canvas.");
  return context;
}

export function clearCanvas(canvas: HTMLCanvasElement) {
  const context = context2d(canvas);
  context.save();
  context.setTransform(1, 0, 0, 1, 0, 0);
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.restore();
}

function snapshotOf(canvas: HTMLCanvasElement) {
  const copy = document.createElement("canvas");
  copy.width = canvas.width;
  copy.height = canvas.height;
  if (canvas.width > 0 && canvas.height > 0) context2d(copy).drawImage(canvas, 0, 0);
  return copy;
}

export function fitCanvases(surface: HTMLElement, paint: HTMLCanvasElement, live: HTMLCanvasElement) {
  const pixelRatio = Math.min(window.devicePixelRatio || 1, MAX_CANVAS_PIXELS_TALL / Math.max(1, surface.clientHeight));
  const width = Math.round(surface.clientWidth * pixelRatio);
  const height = Math.round(surface.clientHeight * pixelRatio);
  if (paint.width === width && paint.height === height) return;
  const snapshot = snapshotOf(paint);
  for (const canvas of [paint, live]) {
    canvas.width = width;
    canvas.height = height;
    context2d(canvas).setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
  }
  if (snapshot.width > 0) context2d(paint).drawImage(snapshot, 0, 0, snapshot.width / pixelRatio, snapshot.height / pixelRatio);
}

export function prefersReducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

// eslint-disable-next-line complexity
export function renderInkFrame(brush: SchematicBrush, paint: HTMLCanvasElement, live: HTMLCanvasElement, palette: InkPalette, reducedMotion: boolean, hadActiveLive = { current: true }) {
  const finished = reducedMotion ? brush.takeAll() : brush.tick();
  if (finished.length > 0) {
    const paintContext = context2d(paint);
    for (const stamp of finished) renderStamp(paintContext, stamp, Infinity, palette);
  }
  const hasActive = brush.activeStamps.length > 0;
  if (hasActive || hadActiveLive.current) {
    clearCanvas(live);
    hadActiveLive.current = hasActive;
    if (hasActive) {
      const liveContext = context2d(live);
      const now = performance.now();
      for (const stamp of brush.activeStamps) renderStamp(liveContext, stamp, now, palette);
    }
  }
}

export function startInkLoop(brush: SchematicBrush, paint: HTMLCanvasElement, live: HTMLCanvasElement, palette: InkPalette) {
  const reducedMotion = prefersReducedMotion();
  const hadActiveLive = { current: true };
  let frame = requestAnimationFrame(function loop() {
    renderInkFrame(brush, paint, live, palette, reducedMotion, hadActiveLive);
    frame = requestAnimationFrame(loop);
  });
  return () => cancelAnimationFrame(frame);
}
