"use client";

import { useFrame } from "@react-three/fiber";
import { easing } from "maath";
import { createContext, useContext, useState, type ReactNode } from "react";
import { shots, type ShotName, type StageLevels } from "./shots";

type Approach = { kind: "sweep"; unitsPerSecond: number } | { kind: "damp"; smoothTime: number };

const approachByLevel: Record<keyof StageLevels, Approach> = {
  presence: { kind: "damp", smoothTime: 0.28 },
  scattered: { kind: "damp", smoothTime: 0.4 },
  pointsRevealed: { kind: "sweep", unitsPerSecond: 0.5 },
  solidified: { kind: "sweep", unitsPerSecond: 0.36 },
  focusOnCounter: { kind: "damp", smoothTime: 0.5 },
  measuring: { kind: "damp", smoothTime: 0.55 },
  readerMoved: { kind: "damp", smoothTime: 0.55 },
  route: { kind: "sweep", unitsPerSecond: 0.55 },
  frameShift: { kind: "damp", smoothTime: 0.9 },
  frameDrop: { kind: "damp", smoothTime: 0.9 },
  turntable: { kind: "damp", smoothTime: 0.8 },
  sway: { kind: "damp", smoothTime: 1.2 },
};

const waitsUntil: Partial<Record<keyof StageLevels, (levels: StageLevels) => boolean>> = {
  solidified: (levels) => levels.presence > 0.9 && levels.scattered < 0.04,
  route: (levels) => levels.presence > 0.9 && levels.solidified > 0.98 && levels.readerMoved > 0.9,
};

const levelNames = Object.keys(approachByLevel) as (keyof StageLevels)[];
const LONGEST_FRAME_SECONDS = 1 / 20;

function sweepToward(current: number, target: number, maxStep: number) {
  const distance = target - current;
  if (Math.abs(distance) <= maxStep) return target;
  return current + Math.sign(distance) * maxStep;
}

function advanceLevel(levels: StageLevels, name: keyof StageLevels, target: number, delta: number) {
  const isHeldBack = target > levels[name] && waitsUntil[name]?.(levels) === false;
  if (isHeldBack) return;
  const approach = approachByLevel[name];
  if (approach.kind === "sweep") {
    levels[name] = sweepToward(levels[name], target, approach.unitsPerSecond * delta);
    return;
  }
  easing.damp(levels, name, target, approach.smoothTime, delta);
}

const StageLevelsContext = createContext<StageLevels | null>(null);

function initialLevels(): StageLevels {
  return { ...shots.awayBeforeScan.levels, pointsRevealed: 0 };
}

export function StageLevelsProvider({ shot, children }: { shot: ShotName; children: ReactNode }) {
  const [levels] = useState(initialLevels);

  useFrame((_, delta) => {
    const target = shots[shot].levels;
    const step = Math.min(delta, LONGEST_FRAME_SECONDS);
    for (const name of levelNames) advanceLevel(levels, name, target[name], step);
  }, -1);

  return <StageLevelsContext.Provider value={levels}>{children}</StageLevelsContext.Provider>;
}

export function useStageLevels() {
  const levels = useContext(StageLevelsContext);
  if (!levels) throw new Error("useStageLevels needs a StageLevelsProvider");
  return levels;
}
