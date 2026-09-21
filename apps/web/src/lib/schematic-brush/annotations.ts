import { arcPoints, boundsOf, boxAround, HALF_PI, QUARTER_PI, rectPoints, type Box, type Point } from "./geometry";
import { drawCalloutBubble, drawSectionMarker, type DraftNode } from "./nodes";
import { chance, random, randomInt, randomSign, shuffled } from "./random";
import type { DraftRecorder } from "./recorder";
import { codeSection, detailNumber, inchesForLength, requirementNote } from "./vocabulary";

export function labelNode(d: DraftRecorder, node: DraftNode) {
  if (node.kind === "bubble" || node.kind === "section") return;
  const text = node.kind === "turning" ? '60" dia' : requirementNote();
  const size = d.fontSize(0.62);
  const placed = d.textClear(text, node.x, node.y - node.ry - d.unit * 0.45, size, { align: "center", baseline: "bottom" });
  if (!placed) return;
  const halfWidth = d.measure(text, size) / 2;
  const underlineY = placed[1] + d.unit * 0.12;
  d.line([[placed[0] - halfWidth, underlineY], [placed[0] + halfWidth, underlineY]], "thin");
}

function horizontalDimension(d: DraftRecorder, a: DraftNode, b: DraftNode, sign: number, offset: number) {
  const tick = d.unit * 0.22;
  const y = d.snap((sign < 0 ? Math.min(a.y, b.y) : Math.max(a.y, b.y)) + offset);
  for (const end of [a, b]) {
    d.line([[end.x, end.y + sign * d.unit * 0.6], [end.x, y + sign * d.unit * 0.5]], "thin");
  }
  d.line([[a.x, y], [b.x, y]], "thin");
  for (const end of [a, b]) d.line([[end.x - tick, y + tick], [end.x + tick, y - tick]], "regular");
  const label = inchesForLength(Math.abs(b.x - a.x), d.unit);
  d.textClear(label, (a.x + b.x) / 2, y - d.unit * 0.15, d.fontSize(0.6), { align: "center", baseline: "bottom" });
}

function verticalDimension(d: DraftRecorder, a: DraftNode, b: DraftNode, sign: number, offset: number) {
  const tick = d.unit * 0.22;
  const x = d.snap((sign < 0 ? Math.min(a.x, b.x) : Math.max(a.x, b.x)) + offset);
  for (const end of [a, b]) {
    d.line([[end.x + sign * d.unit * 0.6, end.y], [x + sign * d.unit * 0.5, end.y]], "thin");
  }
  d.line([[x, a.y], [x, b.y]], "thin");
  for (const end of [a, b]) d.line([[x - tick, end.y + tick], [x + tick, end.y - tick]], "regular");
  const label = inchesForLength(Math.abs(b.y - a.y), d.unit);
  d.textClear(label, x - d.unit * 0.15, (a.y + b.y) / 2, d.fontSize(0.6), { align: "center", baseline: "bottom", rotation: -HALF_PI });
}

function dimension(d: DraftRecorder, node: DraftNode, previous: DraftNode | null) {
  if (!previous) return;
  const horizontal = Math.abs(node.x - previous.x) >= Math.abs(node.y - previous.y);
  const span = horizontal ? Math.abs(node.x - previous.x) : Math.abs(node.y - previous.y);
  if (span < d.unit * 2) return;
  const sign = randomSign();
  const offset = d.unit * random(2.5, 4.5) * sign;
  const draw = horizontal ? horizontalDimension : verticalDimension;
  draw(d, previous, node, sign, offset);
}

function findFreeSpot(d: DraftRecorder, node: DraftNode, radius: number): Point | null {
  for (let attempt = 0; attempt < 8; attempt++) {
    const angle = random(0, Math.PI * 2);
    const reach = d.unit * random(3, 5) + node.r;
    const x = d.snap(node.x + Math.cos(angle) * reach);
    const y = d.snap(node.y + Math.sin(angle) * reach);
    if (d.occupancy.isFree(boxAround(x, y, radius))) return [x, y];
  }
  return null;
}

function callout(d: DraftRecorder, node: DraftNode) {
  const radius = d.unit * 1.1;
  const spot = findFreeSpot(d, node, radius);
  if (!spot) return;
  const [cx, cy] = spot;
  d.occupancy.claim(boxAround(cx, cy, radius));
  const angle = Math.atan2(node.y - cy, node.x - cx);
  d.line([[node.x, node.y], [cx + Math.cos(angle) * radius, cy + Math.sin(angle) * radius]], "thin");
  if (chance(0.5)) d.dot(node.x, node.y, d.unit * 0.12);
  drawCalloutBubble(d, cx, cy, radius);
}

function contourPoints(node: DraftNode, width: number, height: number, rotation: number): Point[] {
  const local: Point[] = [[-width / 2, height], [-width / 2, 0], ...arcPoints(0, 0, width / 2, Math.PI, Math.PI * 2), [width / 2, height]];
  const cos = Math.cos(rotation);
  const sin = Math.sin(rotation);
  return local.map(([px, py]) => [node.x + px * cos - py * sin, node.y + px * sin + py * cos] as const);
}

function contours(d: DraftRecorder, node: DraftNode) {
  const count = randomInt(3, 7);
  const rotation = randomInt(0, 4) * HALF_PI;
  const step = d.unit * 0.45;
  for (let i = 0; i < count; i++) {
    const outermost = i === count - 1;
    const points = contourPoints(node, node.rx * 2 + d.unit * 1.2 + i * step * 2, node.ry + d.unit * 1.2 + i * step, rotation);
    d.line(points, outermost ? "regular" : "thin");
    if (outermost) d.occupancy.claim(boundsOf(points));
  }
}

function hatch(d: DraftRecorder, node: DraftNode) {
  const width = d.unit * random(1.5, 3);
  const height = d.unit * random(0.6, 1.2);
  const x = d.snap(node.x + d.unit * random(-4, 4));
  const y = d.snap(node.y + d.unit * random(1.5, 3.5) * randomSign());
  const box: Box = [x, y, x + width, y + height];
  if (!d.occupancy.isFree(box)) return;
  d.occupancy.claim(box);
  d.line(rectPoints(x, y, width, height), "thin");
  const step = d.unit * 0.28;
  for (let offset = step; offset < width + height; offset += step) {
    const from: Point = offset <= width ? [x + offset, y] : [x + width, y + offset - width];
    const to: Point = offset <= height ? [x, y + offset] : [x + offset - height, y + height];
    d.line([from, to], "thin");
  }
}

function leaderNote(d: DraftRecorder, node: DraftNode) {
  const text = codeSection();
  const size = d.fontSize(0.6);
  const textWidth = d.measure(text, size);
  for (const angle of shuffled([-QUARTER_PI, -3 * QUARTER_PI, QUARTER_PI, 3 * QUARTER_PI])) {
    const reach = d.unit * random(2.5, 4.5) + node.r;
    const elbowX = d.snap(node.x + Math.cos(angle) * reach);
    const elbowY = d.snap(node.y + Math.sin(angle) * reach);
    const direction = Math.cos(angle) > 0 ? 1 : -1;
    const textX = elbowX + direction * d.unit * 0.15;
    const textY = elbowY - d.unit * 0.1;
    const placement = { align: direction > 0 ? "left" : "right", baseline: "bottom" } as const;
    const box = d.textBox(text, textX, textY, size, placement);
    if (!d.occupancy.isFree(box)) continue;
    d.occupancy.claim(box);
    const start: Point = [node.x + Math.cos(angle) * node.r * 0.6, node.y + Math.sin(angle) * node.r * 0.6];
    d.line([start, [elbowX, elbowY], [elbowX + direction * (textWidth + d.unit * 0.3), elbowY]], "thin");
    d.textAt(text, textX, textY, size, placement);
    return;
  }
}

function keynoteArrow(d: DraftRecorder, node: DraftNode) {
  const x = d.snap(node.x + d.unit * random(-3, 3));
  const y = d.snap(node.y + d.unit * random(2, 4) * randomSign());
  const length = d.unit * 1.6;
  const direction = randomSign();
  const text = detailNumber();
  const size = d.fontSize(0.6);
  const placement = { align: direction > 0 ? "right" : "left" } as const;
  const textX = x - direction * d.unit * 0.15;
  const box = d.textBox(text, textX, y, size, placement);
  const headX = x + direction * length;
  if (!d.occupancy.isFree([Math.min(box[0], headX), box[1], Math.max(box[2], headX), box[3]])) return;
  d.occupancy.claim(box);
  d.line([[x, y], [headX, y]], "thin");
  d.blot([[headX, y], [headX - direction * d.unit * 0.35, y - d.unit * 0.2], [headX - direction * d.unit * 0.35, y + d.unit * 0.2]]);
  d.textAt(text, textX, y, size, placement);
}

function gridLine(d: DraftRecorder, node: DraftNode) {
  const horizontal = chance(0.5);
  const length = d.unit * random(10, 26) * randomSign();
  const endX = d.snap(horizontal ? node.x + length : node.x);
  const endY = d.snap(horizontal ? node.y : node.y + length);
  const markerBox = boxAround(endX, endY, d.unit * 1.8);
  if (!d.occupancy.isFree(markerBox)) return;
  d.occupancy.claim(markerBox);
  d.line([[node.x, node.y], [endX, endY]], "thin", chance(0.5) ? [d.unit * 0.9, d.unit * 0.35] : undefined);
  drawSectionMarker(d, endX, endY, d.unit * 0.85);
}

function doorSwing(d: DraftRecorder, node: DraftNode) {
  const leaf = d.unit * 6;
  const hingeX = d.snap(node.x + randomSign() * (node.rx + d.unit));
  const hingeY = d.snap(node.y + randomSign() * (node.ry + d.unit));
  const from = randomInt(0, 4) * HALF_PI;
  const swing = arcPoints(hingeX, hingeY, leaf, from, from + HALF_PI);
  const box = boundsOf([[hingeX, hingeY], ...swing]);
  if (!d.occupancy.isFree(box)) return;
  d.occupancy.claim(box);
  d.line([[hingeX, hingeY], swing[swing.length - 1]], "regular");
  d.line(swing, "thin");
}

interface Decoration {
  odds: number;
  draw: (d: DraftRecorder, node: DraftNode, previous: DraftNode | null) => void;
}

const DECORATIONS: Decoration[] = [
  { odds: 0.45, draw: dimension },
  { odds: 0.35, draw: callout },
  { odds: 0.2, draw: contours },
  { odds: 0.15, draw: hatch },
  { odds: 0.3, draw: leaderNote },
  { odds: 0.1, draw: keynoteArrow },
  { odds: 0.12, draw: gridLine },
  { odds: 0.12, draw: doorSwing },
];

export function decorate(d: DraftRecorder, node: DraftNode, previous: DraftNode | null) {
  for (const decoration of DECORATIONS) {
    if (chance(decoration.odds)) d.attempt(() => decoration.draw(d, node, previous));
  }
  if (!previous && chance(0.5)) d.attempt(() => callout(d, node));
}

export function dimensionAbove(d: DraftRecorder, left: Point, right: Point, lineY: number, label: string) {
  const tick = d.unit * 0.22;
  for (const end of [left, right]) d.line([[end[0], end[1] - d.unit * 0.4], [end[0], lineY - d.unit * 0.5]], "thin");
  d.line([[left[0], lineY], [right[0], lineY]], "thin");
  for (const end of [left, right]) d.line([[end[0] - tick, lineY + tick], [end[0] + tick, lineY - tick]], "regular");
  d.textAt(label, (left[0] + right[0]) / 2, lineY - d.unit * 0.25, d.fontSize(0.72), { align: "center", baseline: "bottom" });
}

export function dimensionLeft(d: DraftRecorder, top: Point, bottom: Point, lineX: number, label: string) {
  const tick = d.unit * 0.22;
  for (const end of [top, bottom]) d.line([[end[0] - d.unit * 0.4, end[1]], [lineX - d.unit * 0.5, end[1]]], "thin");
  d.line([[lineX, top[1]], [lineX, bottom[1]]], "thin");
  for (const end of [top, bottom]) d.line([[lineX - tick, end[1] + tick], [lineX + tick, end[1] - tick]], "regular");
  d.textAt(label, lineX - d.unit * 0.25, (top[1] + bottom[1]) / 2, d.fontSize(0.72), { align: "center", baseline: "bottom", rotation: -HALF_PI });
}

export function leaderNoteAt(d: DraftRecorder, target: Point, shelf: Point, text: string) {
  const size = d.fontSize(0.66);
  const direction = shelf[0] >= target[0] ? 1 : -1;
  const shelfEnd: Point = [shelf[0] + direction * (d.measure(text, size) + d.unit * 0.4), shelf[1]];
  d.dot(target[0], target[1], d.unit * 0.16);
  d.line([target, shelf, shelfEnd], "thin");
  d.textAt(text, shelf[0] + direction * d.unit * 0.2, shelf[1] - d.unit * 0.15, size, { align: direction > 0 ? "left" : "right", baseline: "bottom" });
}
