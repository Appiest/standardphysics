import { HALF_PI, TAU, type Point } from "./geometry";
import { smoothNoise } from "./random";

export const DRAFTING_UNIT_PX = 12;
export const DEMO_ROUTE_DURATION_MS = 9000;

export interface ZoneGrid {
  columns: number;
  rows: number;
}

export function zoneGridFor(width: number, height: number): ZoneGrid {
  return { columns: width >= 720 ? 8 : 4, rows: height >= 600 ? 6 : 4 };
}

export function zoneRowLetter(row: number) {
  return String.fromCharCode(65 + row);
}

export function zoneAt(x: number, y: number, width: number, height: number): string {
  const { columns, rows } = zoneGridFor(width, height);
  const column = Math.min(columns - 1, Math.max(0, Math.floor((x / width) * columns)));
  const row = Math.min(rows - 1, Math.max(0, Math.floor((y / height) * rows)));
  return `${zoneRowLetter(row)}${column + 1}`;
}

export function demoRoute(width: number, height: number, pointCount = 700): Point[] {
  const centerX = width * 0.42;
  const centerY = height * 0.44;
  const radiusX = width * 0.3;
  const radiusY = height * 0.32;
  return Array.from({ length: pointCount }, (_, index) => {
    const t = index / (pointCount - 1);
    const angle = -HALF_PI + t * TAU * 1.35;
    const swell = 0.55 + 0.45 * Math.sin(t * Math.PI) + (smoothNoise(t * 9) - 0.5) * 0.25;
    return [centerX + Math.cos(angle) * radiusX * swell, centerY + Math.sin(angle) * radiusY * swell] as const;
  });
}
