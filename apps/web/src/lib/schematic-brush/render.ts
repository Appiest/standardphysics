import { clamp, cutPolyline, TAU, type Point } from "./geometry";
import { INK, type BlotMark, type InkMark, type InkRun, type Mark, type SpeckMark, type Stamp, type TextMark } from "./marks";
import { smoothNoise } from "./random";
import { ALIGN_OFFSET } from "./recorder";

export interface InkPalette {
  ink: string;
  paper: string;
  fontFamily: string;
}

const BLEED_LAYERS: readonly [reach: number, alpha: number][] = [
  [1, 0.16],
  [0.6, 0.38],
  [0.33, 1],
];

function easeOutBack(progress: number) {
  const overshoot = 1.4;
  return 1 + (overshoot + 1) * (progress - 1) ** 3 + overshoot * (progress - 1) ** 2;
}

function strokeSegment(ctx: CanvasRenderingContext2D, from: Point, to: Point, width: number, alpha: number) {
  ctx.globalAlpha = alpha;
  ctx.lineWidth = width;
  ctx.beginPath();
  ctx.moveTo(from[0], from[1]);
  ctx.lineTo(to[0], to[1]);
  ctx.stroke();
}

function fillDisc(ctx: CanvasRenderingContext2D, x: number, y: number, diameter: number, alpha: number) {
  ctx.globalAlpha = alpha;
  ctx.beginPath();
  ctx.arc(x, y, Math.max(0, diameter / 2), 0, TAU);
  ctx.fill();
}

function tracePolygon(ctx: CanvasRenderingContext2D, points: readonly Point[]) {
  ctx.beginPath();
  points.forEach(([x, y], index) => (index === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)));
  ctx.closePath();
}

function bleedRun(ctx: CanvasRenderingContext2D, run: InkRun, points: readonly Point[], weight: number) {
  const reach = weight * INK.bleedReach;
  for (const [reachShare, alphaShare] of BLEED_LAYERS) {
    for (let i = 1; i < points.length; i++) {
      const slow = smoothNoise(run.seed + 40 + i * 0.11) ** 1.7;
      const fast = smoothNoise(run.seed + 60 + i * 0.55);
      const spread = 0.15 + 1.3 * slow + 0.6 * fast * slow;
      strokeSegment(ctx, points[i - 1], points[i], weight + reach * reachShare * spread, INK.bleedAlpha * alphaShare);
    }
  }
}

function fibersAndGrains(ctx: CanvasRenderingContext2D, run: InkRun, points: readonly Point[], weight: number) {
  const reach = weight * INK.bleedReach;
  for (let i = 1; i < points.length; i++) {
    const [ax, ay] = points[i - 1];
    const [bx, by] = points[i];
    const length = Math.hypot(bx - ax, by - ay) || 1;
    const nx = -(by - ay) / length;
    const ny = (bx - ax) / length;
    const side = smoothNoise(run.seed + 130 + i * 1.3) < 0.5 ? -1 : 1;
    if (smoothNoise(run.seed + 90 + i * 2.3) > 1 - INK.fiberChance) {
      const fiber = reach * (0.3 + 1.2 * smoothNoise(run.seed + 170 + i * 0.9)) * side;
      strokeSegment(ctx, [bx, by], [bx + nx * fiber, by + ny * fiber], Math.max(0.35, weight * 0.3), 0.14 + 0.2 * smoothNoise(run.seed + 210 + i * 0.7));
    }
    if (smoothNoise(run.seed + 250 + i * 3.1) > 1 - INK.grainChance) {
      const offset = (weight * 0.6 + reach * 0.9 * smoothNoise(run.seed + 330 + i * 1.1)) * -side;
      const radius = 0.25 + 0.55 * smoothNoise(run.seed + 370 + i * 0.8);
      fillDisc(ctx, bx + nx * offset, by + ny * offset, radius * 2, 0.16 + 0.35 * smoothNoise(run.seed + 410 + i * 0.6));
    }
  }
}

function inkBody(ctx: CanvasRenderingContext2D, run: InkRun, points: readonly Point[], weight: number) {
  for (let i = 1; i < points.length; i++) {
    strokeSegment(ctx, points[i - 1], points[i], weight * (0.5 + 1.2 * smoothNoise(run.seed + i * 0.2)), INK.bodyAlpha);
  }
}

function poolBleed(ctx: CanvasRenderingContext2D, seed: number, x: number, y: number, diameter: number) {
  fillDisc(ctx, x, y, diameter * 2.2, 0.07);
  for (let lobe = 0; lobe < 3; lobe++) {
    const angle = smoothNoise(seed + lobe * 3.1) * TAU * 2;
    const offset = diameter * (0.3 + 0.5 * smoothNoise(seed + 50 + lobe * 2.7));
    const size = diameter * (0.9 + 0.8 * smoothNoise(seed + 110 + lobe * 1.9));
    fillDisc(ctx, x + Math.cos(angle) * offset, y + Math.sin(angle) * offset, size, 0.055 + 0.05 * smoothNoise(seed + 80 + lobe));
  }
}

function inkPools(ctx: CanvasRenderingContext2D, run: InkRun, points: readonly Point[], weight: number, complete: boolean) {
  if (!run.pooled) return;
  const [startX, startY] = points[0];
  poolBleed(ctx, run.seed + 7, startX, startY, weight * 3);
  fillDisc(ctx, startX, startY, weight * 3, 0.88);
  if (!complete) return;
  const [endX, endY] = points[points.length - 1];
  poolBleed(ctx, run.seed + 11, endX, endY, weight * 2.5);
  fillDisc(ctx, endX, endY, weight * 2.5, 0.88);
}

function inkBlobs(ctx: CanvasRenderingContext2D, run: InkRun, points: readonly Point[], weight: number) {
  for (const blob of run.blobs) {
    if (blob.index >= points.length) continue;
    const [x, y] = points[blob.index];
    fillDisc(ctx, x, y, weight * blob.size, 0.88);
    if (blob.drip <= 0) continue;
    strokeSegment(ctx, [x, y], [x, y + blob.drip * 0.7], weight * 1.1, 0.82);
    strokeSegment(ctx, [x, y + blob.drip * 0.7], [x, y + blob.drip], weight * 0.6, 0.82);
    fillDisc(ctx, x, y + blob.drip, weight * 1.6, 0.88);
  }
}

function drawRun(ctx: CanvasRenderingContext2D, run: InkRun, points: readonly Point[], weight: number, complete: boolean) {
  if (points.length < 2) return;
  bleedRun(ctx, run, points, weight);
  fibersAndGrains(ctx, run, points, weight);
  inkBody(ctx, run, points, weight);
  inkPools(ctx, run, points, weight, complete);
  inkBlobs(ctx, run, points, weight);
}

function drawInk(ctx: CanvasRenderingContext2D, mark: InkMark, progress: number, palette: InkPalette) {
  if (progress >= 1 && mark.fillPolygon) {
    ctx.globalAlpha = 1;
    ctx.fillStyle = palette.paper;
    tracePolygon(ctx, mark.fillPolygon);
    ctx.fill();
    ctx.fillStyle = palette.ink;
  }
  let budget = progress >= 1 ? Infinity : progress * mark.length;
  for (const run of mark.runs) {
    if (budget < run.length) {
      drawRun(ctx, run, cutPolyline(run.points, budget), mark.weight, false);
      return;
    }
    drawRun(ctx, run, run.points, mark.weight, true);
    budget -= run.length;
  }
}

function drawText(ctx: CanvasRenderingContext2D, mark: TextMark, progress: number, palette: InkPalette) {
  const visibleCharacters = progress >= 1 ? mark.text.length : Math.ceil(progress * mark.text.length);
  if (visibleCharacters <= 0) return;
  ctx.font = `500 ${mark.size}px ${palette.fontFamily}`;
  ctx.textAlign = "left";
  ctx.textBaseline = mark.baseline;
  const left = ALIGN_OFFSET[mark.align] * ctx.measureText(mark.text).width;
  const visible = mark.text.slice(0, visibleCharacters);
  ctx.save();
  ctx.translate(mark.x, mark.y);
  ctx.rotate(mark.rotation);
  ctx.globalAlpha = 0.06;
  ctx.lineWidth = Math.max(0.8, mark.size * 0.22);
  ctx.strokeText(visible, left, 0);
  ctx.globalAlpha = 0.27;
  ctx.lineWidth = Math.max(0.6, mark.size * 0.1);
  ctx.strokeText(visible, left, 0);
  ctx.globalAlpha = INK.bodyAlpha;
  ctx.fillText(visible, left, 0);
  ctx.restore();
}

function drawSpecks(ctx: CanvasRenderingContext2D, mark: SpeckMark, progress: number) {
  const scale = progress >= 1 ? 1 : easeOutBack(progress);
  for (const speck of mark.specks) fillDisc(ctx, speck.x, speck.y, speck.radius * 2 * scale, speck.alpha);
}

function drawBlot(ctx: CanvasRenderingContext2D, mark: BlotMark, progress: number) {
  const scale = progress >= 1 ? 1 : easeOutBack(progress);
  ctx.save();
  ctx.translate(mark.cx, mark.cy);
  ctx.scale(scale, scale);
  tracePolygon(ctx, mark.points.map(([x, y]) => [x - mark.cx, y - mark.cy] as const));
  ctx.globalAlpha = 1;
  ctx.fill();
  ctx.globalAlpha = 0.27;
  ctx.lineWidth = 2.2;
  ctx.stroke();
  ctx.restore();
}

function drawMark(ctx: CanvasRenderingContext2D, mark: Mark, progress: number, palette: InkPalette) {
  switch (mark.kind) {
    case "ink":
      return drawInk(ctx, mark, progress, palette);
    case "text":
      return drawText(ctx, mark, progress, palette);
    case "speck":
      return drawSpecks(ctx, mark, progress);
    default:
      return drawBlot(ctx, mark, progress);
  }
}

export function renderStamp(ctx: CanvasRenderingContext2D, stamp: Stamp, now: number, palette: InkPalette) {
  ctx.strokeStyle = palette.ink;
  ctx.fillStyle = palette.ink;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  for (const mark of stamp.marks) {
    const progress = now === Infinity ? 1 : clamp((now - stamp.startsAt - mark.start) / mark.duration, 0, 1);
    if (progress > 0) drawMark(ctx, mark, progress, palette);
  }
  ctx.globalAlpha = 1;
}
