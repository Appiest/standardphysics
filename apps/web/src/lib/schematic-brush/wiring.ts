import { bezierPoints, lerp, type Point } from "./geometry";
import type { DraftNode } from "./nodes";
import { chance, random, randomSign } from "./random";
import type { DraftRecorder } from "./recorder";

function cableControls(a: DraftNode, b: DraftNode, unit: number): [Point, Point] {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const variant = random();
  if (variant < 0.45) return [[a.x + dx * 0.5, a.y], [b.x - dx * 0.5, b.y]];
  if (variant >= 0.8) return [[a.x, a.y + dy * 0.5], [b.x, b.y - dy * 0.5]];
  const length = Math.hypot(dx, dy) || 1;
  const sag = unit * random(2, 7) * randomSign();
  const nx = (-dy / length) * sag;
  const ny = (dx / length) * sag;
  return [
    [a.x + dx * 0.2 + nx, a.y + dy * 0.2 + ny],
    [b.x - dx * 0.2 + nx, b.y - dy * 0.2 + ny],
  ];
}

function cable(d: DraftRecorder, a: DraftNode, b: DraftNode) {
  const [control1, control2] = cableControls(a, b, d.unit);
  d.line(bezierPoints([a.x, a.y], control1, control2, [b.x, b.y]), "cable");
}

function orthogonalRoute(d: DraftRecorder, a: DraftNode, b: DraftNode) {
  const variant = random();
  const middleX = d.snap(lerp(a.x, b.x, 0.5));
  const routes: Point[][] = [
    [[a.x, a.y], [b.x, a.y], [b.x, b.y]],
    [[a.x, a.y], [a.x, b.y], [b.x, b.y]],
    [[a.x, a.y], [middleX, a.y], [middleX, b.y], [b.x, b.y]],
  ];
  const points = routes[variant < 0.4 ? 0 : variant < 0.8 ? 1 : 2];
  d.line(points, "regular");
  if (!chance(0.5)) return;
  for (const bend of points.slice(1, -1)) d.dot(bend[0], bend[1], d.unit * 0.12);
}

export function connect(d: DraftRecorder, a: DraftNode, b: DraftNode) {
  const variant = random();
  if (variant < 0.55) return orthogonalRoute(d, a, b);
  if (variant < 0.8) return d.line([[a.x, a.y], [b.x, b.y]], "regular", [d.unit * 0.6, d.unit * 0.3]);
  cable(d, a, b);
}
