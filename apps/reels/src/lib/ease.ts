import { Easing, interpolate } from "remotion";
import { easeDrawn, easeExit, easeSweep } from "../../../web/src/lib/motion";

const bezier = ([x1, y1, x2, y2]: readonly number[]) => Easing.bezier(x1, y1, x2, y2);

export const drawn = bezier(easeDrawn);
export const sweep = bezier(easeSweep);
export const exit = bezier(easeExit);
export const snap = Easing.bezier(0.7, 0, 0.1, 1);
export const slam = Easing.bezier(0.9, 0, 1, 0.4);

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;

export function progress(frame: number, start: number, duration: number, easing: (t: number) => number = drawn) {
  return interpolate(frame, [start, start + duration], [0, 1], { ...clamp, easing });
}

export function between(frame: number, range: readonly [number, number], output: readonly [number, number], easing: (t: number) => number = drawn) {
  return interpolate(frame, [...range], [...output], { ...clamp, easing });
}

export const mix = (from: number, to: number, amount: number) => from + (to - from) * amount;
