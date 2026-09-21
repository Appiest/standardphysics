export type ShotName = "cloud" | "awayBeforeScan" | "scan" | "counter" | "counterFixed" | "awayAfterFix" | "route";

type Triple = [number, number, number];

export type StageLevels = {
  presence: number;
  scattered: number;
  pointsRevealed: number;
  solidified: number;
  focusOnCounter: number;
  measuring: number;
  readerMoved: number;
  route: number;
  frameShift: number;
  frameDrop: number;
  turntable: number;
  sway: number;
};

export type Shot = {
  cameraPosition: Triple;
  cameraTarget: Triple;
  levels: StageLevels;
};

const hiddenLevels: StageLevels = {
  presence: 0,
  scattered: 1,
  pointsRevealed: 1,
  solidified: 0,
  focusOnCounter: 0,
  measuring: 0,
  readerMoved: 0,
  route: 0,
  frameShift: 0.18,
  frameDrop: 0,
  turntable: 1,
  sway: 0,
};

const solidLevels: StageLevels = {
  ...hiddenLevels,
  presence: 1,
  scattered: 0,
  solidified: 1,
  turntable: 0,
};

const overviewCamera = {
  cameraPosition: [13.5, 12.5, 16] as Triple,
  cameraTarget: [0, -0.6, 0] as Triple,
};

const counterCamera = {
  cameraPosition: [2.6, 2.7, 2.6] as Triple,
  cameraTarget: [-0.25, 0.7, -1.9] as Triple,
};

export const shots: Record<ShotName, Shot> = {
  cloud: {
    cameraPosition: [12.5, 10, 14.5],
    cameraTarget: [0, -1.1, 0],
    levels: { ...hiddenLevels, presence: 1, scattered: 0, frameShift: 0.26, frameDrop: 0.1 },
  },
  awayBeforeScan: {
    cameraPosition: [14, 15, 16],
    cameraTarget: [0, 0, 0],
    levels: hiddenLevels,
  },
  scan: {
    ...overviewCamera,
    levels: { ...solidLevels, frameShift: 0.2 },
  },
  counter: {
    ...counterCamera,
    levels: { ...solidLevels, focusOnCounter: 1, measuring: 1, frameShift: 0.17 },
  },
  counterFixed: {
    ...counterCamera,
    levels: { ...solidLevels, focusOnCounter: 1, measuring: 1, readerMoved: 1, frameShift: 0.17 },
  },
  awayAfterFix: {
    cameraPosition: [14, 15, 16],
    cameraTarget: [0, 0, 0],
    levels: { ...hiddenLevels, solidified: 1, readerMoved: 1 },
  },
  route: {
    cameraPosition: [-12.5, 13.5, 14],
    cameraTarget: [0, -0.8, 0],
    levels: { ...solidLevels, readerMoved: 1, route: 1, frameShift: 0.2, sway: 1 },
  },
};
