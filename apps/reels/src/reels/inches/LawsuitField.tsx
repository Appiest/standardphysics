import { useLayoutEffect, useMemo, useRef } from "react";
import { REEL } from "../../lib/timing";

const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));
const FIELD_CENTER = { x: REEL.width / 2, y: 960 };
const DOT_RADIUS = 2.6;

type Seed = { x: number; y: number };

function sunflower(count: number, radius: number): Seed[] {
  const spacing = radius / Math.sqrt(count);
  return Array.from({ length: count }, (_, index) => {
    const distance = spacing * Math.sqrt(index + 0.5);
    const angle = index * GOLDEN_ANGLE;
    return { x: Math.cos(angle) * distance, y: Math.sin(angle) * distance };
  });
}

export type FieldState = {
  /** How many lawsuits have landed so far. */
  shown: number;
  rotation: number;
  /** 0 keeps the sunflower, 1 has every dot fallen onto the horizontal line through the middle. */
  collapse: number;
  opacity: number;
};

function popScale(index: number, shown: number) {
  const age = shown - index;
  if (age >= 60) return 1;
  const t = Math.min(1, age / 60);
  return 1 + 1.6 * Math.sin(t * Math.PI) * (1 - t);
}

function placeSeed(seed: Seed, { rotation, collapse }: FieldState, center: { x: number; y: number }) {
  const cos = Math.cos(rotation);
  const sin = Math.sin(rotation);
  const x = seed.x * cos - seed.y * sin;
  const y = seed.x * sin + seed.y * cos;
  const fall = Math.min(1, Math.max(0, collapse * 1.6 - Math.abs(y) / 900));
  const eased = fall * fall * (3 - 2 * fall);
  return { x: center.x + x * (1 + eased * 0.25), y: center.y + y * (1 - eased) };
}

function drawField(context: CanvasRenderingContext2D, seeds: Seed[], state: FieldState, center: { x: number; y: number }) {
  context.clearRect(0, 0, REEL.width, REEL.height);
  context.globalAlpha = state.opacity;
  context.fillStyle = "#0d0d0c";
  const visible = Math.min(seeds.length, Math.floor(state.shown));
  for (let index = 1; index < visible; index++) {
    const { x, y } = placeSeed(seeds[index], state, center);
    context.beginPath();
    context.arc(x, y, DOT_RADIUS * popScale(index, state.shown), 0, Math.PI * 2);
    context.fill();
  }
}

/** Every ADA lawsuit filed against a business in 2025, one dot each, laid out as a sunflower from the first one outward. */
export function LawsuitField({ count, state, centerY = FIELD_CENTER.y }: { count: number; state: FieldState; centerY?: number }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const seeds = useMemo(() => sunflower(count, 500), [count]);
  useLayoutEffect(() => {
    const context = canvasRef.current?.getContext("2d");
    if (context) drawField(context, seeds, state, { x: FIELD_CENTER.x, y: centerY });
  }, [seeds, state, centerY]);
  return <canvas ref={canvasRef} width={REEL.width} height={REEL.height} className="absolute inset-0" />;
}

export const lawsuitFieldCenter = FIELD_CENTER;
