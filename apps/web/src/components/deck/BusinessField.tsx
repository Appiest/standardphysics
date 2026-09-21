"use client";

import type { MotionValue } from "motion/react";
import { useEffect, useRef } from "react";

const DOT_TO_PITCH = 0.36;
const SMALLEST_TILE_PX = 8;

type FieldColors = { ink: string; gold: string };
type FieldState = { width: number; height: number; businessesLog: number; waveRadius: number };

function readColors(element: HTMLElement): FieldColors {
  const styles = getComputedStyle(element);
  return { ink: styles.getPropertyValue("--color-ink").trim(), gold: styles.getPropertyValue("--color-tape").trim() };
}

const SMALLEST_VISIBLE_PITCH = 6;
const DOTS_PER_GROUP_SIDE = 10;

function pitchFor(state: FieldState) {
  return Math.sqrt((state.width * state.height) / 10 ** state.businessesLog);
}

const tileCache = new Map<string, { tile: HTMLCanvasElement; size: number }>();
const MAX_PIXEL_RATIO = 1.25;

function dotTile(pitch: number, color: string) {
  const size = Math.max(Math.round(pitch), SMALLEST_TILE_PX);
  const key = `${size}:${color}`;
  const cached = tileCache.get(key);
  if (cached) return cached;
  const tile = document.createElement("canvas");
  tile.width = size;
  tile.height = size;
  const context = tile.getContext("2d");
  if (!context) return null;
  context.fillStyle = color;
  context.beginPath();
  context.arc(size / 2, size / 2, size * DOT_TO_PITCH, 0, Math.PI * 2);
  context.fill();
  const made = { tile, size };
  tileCache.set(key, made);
  return made;
}

function fillDotsAtPitch(context: CanvasRenderingContext2D, state: FieldState, pitch: number, color: string) {
  const made = dotTile(pitch, color);
  const pattern = made && context.createPattern(made.tile, "repeat");
  if (!made || !pattern) return;
  const originX = state.width / 2 - pitch / 2;
  const originY = state.height / 2 - pitch / 2;
  pattern.setTransform(new DOMMatrix().translate(originX, originY).scale(pitch / made.size));
  context.fillStyle = pattern;
  context.fillRect(0, 0, state.width, state.height);
}

function fillDots(context: CanvasRenderingContext2D, state: FieldState, color: string) {
  const pitch = pitchFor(state);
  if (pitch >= SMALLEST_VISIBLE_PITCH) {
    fillDotsAtPitch(context, state, pitch, color);
    return;
  }
  const groupings = Math.log10(SMALLEST_VISIBLE_PITCH / pitch);
  const coarse = pitch * DOTS_PER_GROUP_SIDE ** Math.ceil(groupings);
  const fine = coarse / DOTS_PER_GROUP_SIDE;
  const fineVisibility = 1 - (Math.ceil(groupings) - groupings);
  context.globalAlpha = 1 - fineVisibility * 0.85;
  fillDotsAtPitch(context, state, coarse, color);
  context.globalAlpha = fineVisibility * 0.85;
  if (fine >= 2) fillDotsAtPitch(context, state, fine, color);
  context.globalAlpha = 1;
}

function visiblePitch(state: FieldState) {
  const pitch = pitchFor(state);
  if (pitch >= SMALLEST_VISIBLE_PITCH) return pitch;
  return pitch * DOTS_PER_GROUP_SIDE ** Math.ceil(Math.log10(SMALLEST_VISIBLE_PITCH / pitch));
}

function drawSarasDot(context: CanvasRenderingContext2D, state: FieldState, color: string) {
  const radius = Math.max(visiblePitch(state) * DOT_TO_PITCH, 5);
  context.save();
  context.fillStyle = color;
  context.shadowColor = color;
  context.shadowBlur = radius * 1.5;
  context.beginPath();
  context.arc(state.width / 2, state.height / 2, radius, 0, Math.PI * 2);
  context.fill();
  context.restore();
}

function drawField(context: CanvasRenderingContext2D, state: FieldState, colors: FieldColors) {
  context.clearRect(0, 0, state.width, state.height);
  if (state.businessesLog <= 0.01) return;
  fillDots(context, state, colors.ink);
  drawSarasDot(context, state, colors.gold);
  if (state.waveRadius <= 0) return;
  context.save();
  context.beginPath();
  context.arc(state.width / 2, state.height / 2, state.waveRadius, 0, Math.PI * 2);
  context.clip();
  context.clearRect(0, 0, state.width, state.height);
  fillDots(context, state, colors.gold);
  context.restore();
}

export function BusinessField({ businessesLog, waveProgress }: { businessesLog: MotionValue<number>; waveProgress: MotionValue<number> }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d", { alpha: true, desynchronized: true });
    if (!canvas || !context) return;
    const colors = readColors(canvas);
    let size = { width: 1, height: 1 };

    function render() {
      const reach = Math.hypot(size.width, size.height) / 2;
      drawField(context as CanvasRenderingContext2D, { ...size, businessesLog: businessesLog.get(), waveRadius: waveProgress.get() * reach }, colors);
    }

    function resize() {
      if (!canvas || !context) return;
      const ratio = Math.min(window.devicePixelRatio || 1, MAX_PIXEL_RATIO);
      const bounds = canvas.getBoundingClientRect();
      canvas.width = Math.round(bounds.width * ratio);
      canvas.height = Math.round(bounds.height * ratio);
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      size = { width: bounds.width, height: bounds.height };
      render();
    }

    let frame = 0;
    function scheduleRender() {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        render();
      });
    }

    const observer = new ResizeObserver(resize);
    observer.observe(canvas);
    const stopBusinesses = businessesLog.on("change", scheduleRender);
    const stopWave = waveProgress.on("change", scheduleRender);
    return () => {
      observer.disconnect();
      stopBusinesses();
      stopWave();
      cancelAnimationFrame(frame);
    };
  }, [businessesLog, waveProgress]);

  return <canvas ref={canvasRef} aria-hidden className="absolute inset-0 size-full" />;
}
