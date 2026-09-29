export type MotionPoint = { x: number; z: number };

export const EPSILON = 0.0001;

export function add(a: MotionPoint, b: MotionPoint): MotionPoint {
  return { x: a.x + b.x, z: a.z + b.z };
}

export function subtract(a: MotionPoint, b: MotionPoint): MotionPoint {
  return { x: a.x - b.x, z: a.z - b.z };
}

export function scale(point: MotionPoint, amount: number): MotionPoint {
  return { x: point.x * amount, z: point.z * amount };
}

export function dot(a: MotionPoint, b: MotionPoint): number {
  return a.x * b.x + a.z * b.z;
}

export function length(point: MotionPoint): number {
  return Math.hypot(point.x, point.z);
}

export function normalize(point: MotionPoint, fallback: MotionPoint): MotionPoint {
  const magnitude = length(point);
  return magnitude > EPSILON ? scale(point, 1 / magnitude) : fallback;
}
