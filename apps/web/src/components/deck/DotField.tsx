"use client";

import type { MotionValue } from "motion/react";
import { useEffect, useRef } from "react";

const DOT_GROWTH_WINDOW = 0.06;
const FIELD_MARGIN_PX = 14;

type Dot = { x: number; y: number; threshold: number; unpaid: boolean };
type FieldGrid = { columns: number; pitch: number; left: number; top: number };
type FieldLayout = { dots: Dot[]; radius: number; highlighted: Dot | null; grid: FieldGrid; paidCount: number };
export type FieldOrigin = { x: number; y: number };

function noiseAt(index: number) {
  const scatter = Math.sin(index * 12.9898) * 43758.5453;
  return scatter - Math.floor(scatter);
}

function layoutDots(count: number, width: number, height: number, origin: FieldOrigin, unpaidShare: number): FieldLayout {
  const margin = FIELD_MARGIN_PX;
  const innerWidth = width - margin * 2;
  const innerHeight = height - margin * 2;
  const columns = Math.ceil(Math.sqrt((count * innerWidth) / innerHeight));
  const rows = Math.ceil(count / columns);
  const pitch = Math.min(innerWidth / columns, innerHeight / rows);
  const offset = { x: (width - columns * pitch) / 2, y: (height - rows * pitch) / 2 };
  const center = { x: origin.x * width, y: origin.y * height };
  const paidCount = Math.round(count * (1 - unpaidShare));
  const farthest = Math.hypot(Math.max(center.x, width - center.x), Math.max(center.y, height - center.y));

  let highlighted: Dot | null = null;
  let closest = Number.POSITIVE_INFINITY;
  const dots = Array.from({ length: count }, (_, index) => {
    const x = offset.x + ((index % columns) + 0.5) * pitch;
    const y = offset.y + (Math.floor(index / columns) + 0.5) * pitch;
    const distance = Math.hypot(x - center.x, y - center.y);
    const dot = { x, y, threshold: ((distance / farthest) * 0.85 + noiseAt(index) * 0.15) * (1 - DOT_GROWTH_WINDOW), unpaid: index >= paidCount };
    if (distance < closest) {
      closest = distance;
      highlighted = dot;
    }
    return dot;
  });
  return { dots, radius: pitch * 0.36, highlighted, grid: { columns, pitch, left: offset.x, top: offset.y }, paidCount };
}

function grownRadius(dot: Dot, radius: number, progress: number) {
  const grown = Math.min(Math.max((progress - dot.threshold) / DOT_GROWTH_WINDOW, 0), 1);
  return radius * grown * (1 + Math.sin(grown * Math.PI) * 0.45);
}

type FieldColors = { ink: string; tape: string; unpaid: string };

function addDot(context: CanvasRenderingContext2D, dot: Dot, radius: number) {
  context.moveTo(dot.x + radius, dot.y);
  context.arc(dot.x, dot.y, radius, 0, Math.PI * 2);
}

class FieldPainter {
  private readonly settled: HTMLCanvasElement;
  private readonly settledContext: CanvasRenderingContext2D;
  private order: Dot[] = [];
  private unpaid: Dot[] = [];
  private settledCount = 0;
  private layout: FieldLayout = { dots: [], radius: 0, highlighted: null, grid: { columns: 1, pitch: 1, left: 0, top: 0 }, paidCount: 0 };

  constructor(private readonly colors: FieldColors) {
    this.settled = document.createElement("canvas");
    this.settledContext = this.settled.getContext("2d") as CanvasRenderingContext2D;
  }

  reset(layout: FieldLayout, pixelWidth: number, pixelHeight: number, ratio: number) {
    this.layout = layout;
    this.order = layout.dots.filter((dot) => dot !== layout.highlighted).sort((left, right) => left.threshold - right.threshold);
    this.unpaid = this.order.filter((dot) => dot.unpaid);
    this.settled.width = pixelWidth;
    this.settled.height = pixelHeight;
    this.settledContext.setTransform(ratio, 0, 0, ratio, 0, 0);
    this.settledContext.fillStyle = this.colors.ink;
    this.settledCount = 0;
  }

  private settleUpTo(progress: number) {
    const context = this.settledContext;
    context.beginPath();
    while (this.settledCount < this.order.length && progress >= this.order[this.settledCount].threshold + DOT_GROWTH_WINDOW) {
      addDot(context, this.order[this.settledCount], this.layout.radius);
      this.settledCount += 1;
    }
    context.fill();
  }

  private unsettleIfRewound(progress: number) {
    if (this.settledCount === 0) return;
    const last = this.order[this.settledCount - 1];
    if (progress >= last.threshold + DOT_GROWTH_WINDOW) return;
    this.settledContext.save();
    this.settledContext.setTransform(1, 0, 0, 1, 0, 0);
    this.settledContext.clearRect(0, 0, this.settled.width, this.settled.height);
    this.settledContext.restore();
    this.settledCount = 0;
  }

  paint(context: CanvasRenderingContext2D, progress: number, showHighlight: boolean, unpaidFade: number) {
    this.unsettleIfRewound(progress);
    this.settleUpTo(progress);
    context.save();
    context.setTransform(1, 0, 0, 1, 0, 0);
    context.clearRect(0, 0, context.canvas.width, context.canvas.height);
    if (this.settled.width > 0 && this.settled.height > 0) context.drawImage(this.settled, 0, 0);
    context.restore();

    context.fillStyle = this.colors.ink;
    context.beginPath();
    for (let index = this.settledCount; index < this.order.length; index += 1) {
      const dot = this.order[index];
      if (dot.threshold >= progress) break;
      addDot(context, dot, grownRadius(dot, this.layout.radius, progress));
    }
    context.fill();
    this.paintUnpaid(context, unpaidFade);
    this.paintHighlight(context, showHighlight);
  }

  private paintUnpaid(context: CanvasRenderingContext2D, fade: number) {
    if (fade <= 0 || this.unpaid.length === 0) return;
    context.save();
    context.globalAlpha = Math.min(fade, 1);
    context.fillStyle = this.colors.unpaid;
    context.beginPath();
    for (const dot of this.unpaid) addDot(context, dot, this.layout.radius + 0.6);
    context.fill();
    this.outlinePaid(context, Math.min(fade, 1));
    context.restore();
  }

  private outlinePaid(context: CanvasRenderingContext2D, alpha: number) {
    const { columns, pitch, left, top } = this.layout.grid;
    const fullRows = Math.floor(this.layout.paidCount / columns);
    const extra = this.layout.paidCount % columns;
    const pad = FIELD_MARGIN_PX / 2 + 1;
    const right = left + columns * pitch + pad;
    const x0 = left - pad;
    const y0 = top - pad;
    const fullBottom = top + fullRows * pitch;
    const extraRight = left + extra * pitch + pad;
    const extraBottom = fullBottom + pitch + pad;
    context.globalAlpha = alpha;
    context.strokeStyle = this.colors.tape;
    context.lineWidth = 5;
    context.lineJoin = "round";
    context.beginPath();
    context.moveTo(x0, y0);
    context.lineTo(right, y0);
    context.lineTo(right, fullBottom);
    if (extra > 0) {
      context.lineTo(extraRight, fullBottom);
      context.lineTo(extraRight, extraBottom);
      context.lineTo(x0, extraBottom);
    } else {
      context.lineTo(x0, fullBottom + pad);
    }
    context.closePath();
    context.stroke();
  }

  private paintHighlight(context: CanvasRenderingContext2D, showHighlight: boolean) {
    const highlighted = this.layout.highlighted;
    if (!highlighted || !showHighlight) return;
    context.fillStyle = this.colors.tape;
    context.beginPath();
    context.arc(highlighted.x, highlighted.y, Math.max(this.layout.radius * 1.6, 4), 0, Math.PI * 2);
    context.fill();
  }
}

type DotFieldProps = {
  count: number;
  progress: MotionValue<number>;
  origin: FieldOrigin;
  showHighlight?: boolean;
  onHighlightPlaced?: (position: FieldOrigin) => void;
  unpaidShare?: number;
  unpaidFade?: MotionValue<number>;
};

export function DotField({ count, progress, origin, showHighlight = true, onHighlightPlaced, unpaidShare = 0, unpaidFade }: DotFieldProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const showHighlightRef = useRef(showHighlight);
  const repaintRef = useRef<() => void>(() => {});

  useEffect(() => {
    showHighlightRef.current = showHighlight;
    repaintRef.current();
  }, [showHighlight]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context) return;

    const styles = getComputedStyle(canvas);
    const painter = new FieldPainter({ ink: styles.color, tape: styles.getPropertyValue("--color-tape").trim(), unpaid: styles.getPropertyValue("--color-rule").trim() });

    function render(value: number) {
      painter.paint(context as CanvasRenderingContext2D, value, showHighlightRef.current, unpaidFade?.get() ?? 0);
    }

    function resize() {
      if (!canvas || !context) return;
      const ratio = window.devicePixelRatio || 1;
      const { width, height } = canvas.getBoundingClientRect();
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      const layout = layoutDots(count, width, height, origin, unpaidShare);
      painter.reset(layout, canvas.width, canvas.height, ratio);
      if (layout.highlighted) onHighlightPlaced?.({ x: layout.highlighted.x / width, y: layout.highlighted.y / height });
      render(progress.get());
    }

    repaintRef.current = () => render(progress.get());
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);
    const unsubscribe = progress.on("change", render);
    const unsubscribeFade = unpaidFade?.on("change", () => render(progress.get()));
    return () => {
      observer.disconnect();
      unsubscribe();
      unsubscribeFade?.();
    };
  }, [count, progress, origin, onHighlightPlaced, unpaidShare, unpaidFade]);

  return <canvas ref={canvasRef} aria-hidden className="size-full text-ink" />;
}
