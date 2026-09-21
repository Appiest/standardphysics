import { polylineLength, resample, type Point } from "./geometry";
import { chance, random, randomInt } from "./random";

export const PEN_SPEED_PX_PER_MS = 0.7;
export const TYPING_MS_PER_CHARACTER = 22;
export const LABEL_SCALE = 1.5;

export const LINE_WEIGHT = { heavy: 2.3, regular: 1.25, cable: 1, thin: 0.7 } as const;
export type LineWeight = keyof typeof LINE_WEIGHT;

export const INK = {
  resampleSpacing: 2.5,
  raggedness: 1.3,
  bleedReach: 3.8,
  bleedAlpha: 0.16,
  fiberChance: 0.22,
  grainChance: 0.45,
  bodyAlpha: 0.96,
  poolChance: 0.85,
  blobChance: 0.45,
  dripChance: 0.35,
  speckChance: 0.7,
} as const;

export type TextAlign = "left" | "center" | "right";
export type TextBaseline = "top" | "middle" | "bottom";

export interface InkBlob {
  index: number;
  size: number;
  drip: number;
}

export interface InkRun {
  points: Point[];
  length: number;
  seed: number;
  pooled: boolean;
  blobs: InkBlob[];
}

interface Scheduled {
  start: number;
  duration: number;
}

export interface InkMark extends Scheduled {
  kind: "ink";
  runs: InkRun[];
  weight: number;
  length: number;
  fillPolygon: Point[] | null;
}

export interface TextMark extends Scheduled {
  kind: "text";
  text: string;
  x: number;
  y: number;
  size: number;
  align: TextAlign;
  baseline: TextBaseline;
  rotation: number;
}

export interface Speck {
  x: number;
  y: number;
  radius: number;
  alpha: number;
}

export interface SpeckMark extends Scheduled {
  kind: "speck";
  specks: Speck[];
}

export interface BlotMark extends Scheduled {
  kind: "blot";
  points: Point[];
  cx: number;
  cy: number;
}

export type Mark = InkMark | TextMark | SpeckMark | BlotMark;

export interface Stamp {
  marks: Mark[];
  startsAt: number;
  endsAt: number;
}

function createBlobs(pointCount: number, length: number, unit: number): InkBlob[] {
  if (length <= unit * 3 || !chance(INK.blobChance)) return [];
  return Array.from({ length: randomInt(1, 3) }, () => ({
    index: randomInt(2, pointCount - 2),
    size: random(2.2, 3.8),
    drip: chance(INK.dripChance) ? random(unit * 0.8, unit * 3) : 0,
  }));
}

export function createRun(rawPoints: readonly Point[], unit: number): InkRun {
  const points = resample(rawPoints, INK.resampleSpacing);
  const length = polylineLength(points);
  return {
    points,
    length,
    seed: random(0, 1000),
    pooled: length > unit * 1.5 && chance(INK.poolChance),
    blobs: createBlobs(points.length, length, unit),
  };
}

export function specksAround(origin: Point, unit: number): SpeckMark {
  const specks = Array.from({ length: randomInt(6, 16) }, () => {
    const angle = random(0, Math.PI * 2);
    const reach = random(unit * 0.4, unit * 3.5);
    return {
      x: origin[0] + Math.cos(angle) * reach,
      y: origin[1] + Math.sin(angle) * reach,
      radius: random(0.4, 2),
      alpha: random(0.47, 0.94),
    };
  });
  return { kind: "speck", specks, start: 0, duration: 0 };
}

function unscaledDuration(mark: Mark) {
  switch (mark.kind) {
    case "ink":
      return Math.max(80, mark.length / PEN_SPEED_PX_PER_MS);
    case "text":
      return mark.text.length * TYPING_MS_PER_CHARACTER + 60;
    case "speck":
      return 90;
    default:
      return 140;
  }
}

export function markDuration(mark: Mark, speed = 1) {
  return unscaledDuration(mark) / speed;
}
