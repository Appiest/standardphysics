"use client";

import { Robot, Wheelchair } from "@phosphor-icons/react";
import { AnimatePresence, animate, motion, useMotionValue, useTransform, type MotionValue, type Variants } from "motion/react";
import { useEffect, useMemo, useRef, type ReactNode, type RefObject } from "react";
import { easeDrawn, easeSweep, exitTransition } from "@/lib/motion";
import {
  formatInches,
  measurements,
  planCaseGap,
  planCases,
  planCounter,
  planSeating,
  planSize,
  shopDoorway,
  shopOutline,
  wallOutline,
  type PlanRect,
} from "../floorPlan";
import { MaskedLines } from "../primitives";
import type { SlideProps } from "../slides";

const timeline = {
  planIn: 0.1,
  routeDraws: 0.6,
  chairRolls: 0.9,
  chairSeconds: 2.2,
  morphSeconds: 1.4,
  routesAppear: 1.2,
  robotsStart: 1.5,
};

type Phase = "shop" | "warehouse";

const ICON_SIZE = 64;
const ROBOT_SIZE = 44;
const RACK = { width: 110, height: 200, pitch: 230, columns: 6 };

const warehouseOutline = { west: -440, east: shopOutline.east + 440, north: shopOutline.north, south: shopOutline.south };
const warehouseInnerWidth = warehouseOutline.east - warehouseOutline.west;
const rackSpan = (RACK.columns - 1) * RACK.pitch + RACK.width;
const firstRackX = warehouseOutline.west + (warehouseInnerWidth - rackSpan) / 2;
const rackRowsY = [250, 505];
const aisles = {
  top: 170,
  middle: rackRowsY[0] + RACK.height + (rackRowsY[1] - rackRowsY[0] - RACK.height) / 2,
  bottom: (rackRowsY[1] + RACK.height + warehouseOutline.south) / 2,
};

function aisleX(index: number) {
  return firstRackX - (RACK.pitch - RACK.width) / 2 + index * RACK.pitch;
}

const racks: PlanRect[] = rackRowsY.flatMap((y) =>
  Array.from({ length: RACK.columns }, (_, column) => ({ key: `rack-${y}-${column}`, x: firstRackX + column * RACK.pitch, y, width: RACK.width, height: RACK.height })),
);

const DOCK = { y: 40, height: 60, width: 250, gap: 30 };
const docks: PlanRect[] = [-1, 0, 1].map((slot) => ({
  key: `dock-${slot}`,
  x: planSize.width / 2 - DOCK.width / 2 + slot * (DOCK.width + DOCK.gap),
  y: DOCK.y,
  width: DOCK.width,
  height: DOCK.height,
}));

const byColumnThenRow = (left: PlanRect, right: PlanRect) => left.x - right.x || left.y - right.y;
const shopPieces = [...planCases.map((rect) => ({ ...rect, round: false })), ...planSeating].sort(byColumnThenRow);
const racksInMorphOrder = [...racks].sort(byColumnThenRow);

const morphTransition = { duration: timeline.morphSeconds, ease: easeSweep };
const planFadesIn = { duration: 0.5, delay: timeline.planIn };

function rectShape(rect: PlanRect, rx: number) {
  return { x: rect.x, y: rect.y, width: rect.width, height: rect.height, rx };
}

function becomes(from: PlanRect & { round?: boolean }, to: PlanRect): Variants {
  const shopShape = rectShape(from, from.round ? from.width / 2 : 4);
  return {
    enter: { ...shopShape, opacity: 0 },
    shop: { ...shopShape, opacity: 1, transition: { ...morphTransition, opacity: planFadesIn } },
    warehouse: { ...rectShape(to, 4), opacity: 1, transition: morphTransition },
  };
}

const wallsBecome: Variants = {
  enter: { d: wallOutline(shopOutline, shopDoorway), opacity: 0 },
  shop: { d: wallOutline(shopOutline, shopDoorway), opacity: 1, transition: { d: morphTransition, opacity: planFadesIn } },
  warehouse: { d: wallOutline(warehouseOutline, shopDoorway), opacity: 1, transition: { d: morphTransition } },
};

const wheelchairStart = { x: (planCaseGap.westX + planCaseGap.eastX) / 2, y: shopOutline.south - 40 };
const counterApproach = { x: planCounter.reader.x, y: planCounter.frontY + 50 };
const TURN_RADIUS = 50;
const wheelchairRoute = [
  `M ${wheelchairStart.x} ${wheelchairStart.y}`,
  `L ${wheelchairStart.x} ${counterApproach.y + TURN_RADIUS}`,
  `Q ${wheelchairStart.x} ${counterApproach.y} ${wheelchairStart.x - TURN_RADIUS} ${counterApproach.y}`,
  `L ${counterApproach.x} ${counterApproach.y}`,
].join(" ");
const caseDepth = planCases[0].height;

const shownInShop: Variants = {
  enter: { opacity: 0 },
  shop: { opacity: 1, transition: { duration: 0.3, delay: timeline.routeDraws } },
  warehouse: { opacity: 0, transition: exitTransition },
};

type GridStop = [aisle: number, row: keyof typeof aisles];

function gridRoute(stops: GridStop[]) {
  const points = stops.map(([aisle, row]) => `${aisleX(aisle)} ${aisles[row]}`);
  return `M ${points.join(" L ")} Z`;
}

const ROBOT_LAP_SECONDS = 13;

const robotLoops = [
  { d: gridRoute([[0, "top"], [0, "bottom"], [2, "bottom"], [2, "middle"], [1, "middle"], [1, "top"]]), robotOffsets: [0, 0.041] },
  { d: gridRoute([[6, "bottom"], [6, "top"], [4, "top"], [4, "middle"], [5, "middle"], [5, "bottom"]]), robotOffsets: [0.699, 0.643] },
  { d: gridRoute([[2, "top"], [4, "top"], [4, "bottom"], [3, "bottom"], [3, "middle"], [2, "middle"]]), robotOffsets: [0.951] },
  { d: gridRoute([[0, "middle"], [3, "middle"], [3, "top"], [2, "top"], [2, "bottom"], [0, "bottom"]]), robotOffsets: [0.932] },
];

const robots = robotLoops.flatMap((loop) => loop.robotOffsets.map((offset) => ({ d: loop.d, seconds: ROBOT_LAP_SECONDS, offset })));

function shownInWarehouse(delay: number): Variants {
  return {
    enter: { opacity: 0 },
    shop: { opacity: 0, transition: exitTransition },
    warehouse: { opacity: 1, transition: { duration: 0.6, delay } },
  };
}

function pointAlong(path: SVGPathElement | null, progress: number) {
  if (!path) return { x: 0, y: 0 };
  const wrapped = ((progress % 1) + 1) % 1;
  return path.getPointAtLength(wrapped * path.getTotalLength());
}

function useFollowPath(path: RefObject<SVGPathElement | null>, progress: MotionValue<number>) {
  const x = useTransform(progress, (value) => pointAlong(path.current, value).x);
  const y = useTransform(progress, (value) => pointAlong(path.current, value).y);
  return { x, y };
}

function useTravel(options: { from: number; seconds: number; delay: number; repeat: boolean; active: boolean }) {
  const progress = useMotionValue(options.from);
  const { from, seconds, delay, repeat, active } = options;

  useEffect(() => {
    if (!active) {
      progress.jump(from);
      return;
    }
    const controls = animate(progress, [from, from + 1], {
      duration: seconds,
      delay,
      ease: repeat ? "linear" : easeDrawn,
      repeat: repeat ? Infinity : 0,
    });
    return () => controls.stop();
  }, [progress, from, seconds, delay, repeat, active]);

  return progress;
}

function IconMarker({ path, progress, size, children }: { path: RefObject<SVGPathElement | null>; progress: MotionValue<number>; size: number; children: ReactNode }) {
  const { x, y } = useFollowPath(path, progress);
  return (
    <motion.g style={{ x, y }}>
      <circle r={size * 0.62} fill="var(--color-paper-raised)" stroke="var(--color-ink)" strokeWidth={4} />
      {children}
    </motion.g>
  );
}

function WheelchairRoute({ phase }: { phase: Phase }) {
  const path = useRef<SVGPathElement>(null);
  const progress = useTravel({ from: 0, seconds: timeline.chairSeconds, delay: timeline.chairRolls, repeat: false, active: phase === "shop" });
  const clampedProgress = useTransform(progress, (value) => Math.min(value, 0.999));
  const drawnLength = useTransform(progress, (value) => Math.min(Math.max(value, 0.001), 1));

  return (
    <motion.g variants={shownInShop}>
      <motion.path d={wheelchairRoute} fill="none" stroke="var(--color-tape)" strokeOpacity={0.45} strokeWidth={planCaseGap.eastX - planCaseGap.westX} style={{ pathLength: drawnLength }} />
      <motion.path ref={path} d={wheelchairRoute} fill="none" stroke="var(--color-ink)" strokeWidth={4} style={{ pathLength: drawnLength }} />
      <text x={planCaseGap.eastX + 24} y={planCaseGap.y + caseDepth / 2 + 56} fill="var(--color-ink)" className="font-display text-4xl font-bold figures-tabular">
        {formatInches(measurements.caseGapInches)}
      </text>
      <IconMarker path={path} progress={clampedProgress} size={ICON_SIZE}>
        <Wheelchair weight="fill" size={ICON_SIZE} x={-ICON_SIZE / 2} y={-ICON_SIZE / 2} color="var(--color-ink)" />
      </IconMarker>
    </motion.g>
  );
}

function useDetachedPath(d: string) {
  return useMemo(() => {
    const element = document.createElementNS("http://www.w3.org/2000/svg", "path");
    element.setAttribute("d", d);
    return { current: element };
  }, [d]);
}

function RobotOnRoute({ robot, phase }: { robot: (typeof robots)[number]; phase: Phase }) {
  const path = useDetachedPath(robot.d);
  const progress = useTravel({ from: robot.offset, seconds: robot.seconds, delay: timeline.robotsStart, repeat: true, active: phase === "warehouse" });

  return (
    <motion.g variants={shownInWarehouse(timeline.robotsStart)}>
      <IconMarker path={path} progress={progress} size={ROBOT_SIZE}>
        <Robot weight="fill" size={ROBOT_SIZE * 0.8} x={-ROBOT_SIZE * 0.4} y={-ROBOT_SIZE * 0.4} color="var(--color-ink)" />
      </IconMarker>
    </motion.g>
  );
}

function RobotRoutes() {
  return (
    <motion.g variants={shownInWarehouse(timeline.routesAppear)} fill="none" stroke="var(--color-ink-faint)" strokeWidth={4} strokeDasharray="14 12">
      {robotLoops.map((loop) => (
        <path key={loop.d} d={loop.d} />
      ))}
    </motion.g>
  );
}

function MorphingPlan() {
  return (
    <g stroke="var(--color-ink)" strokeLinejoin="round">
      <motion.path fill="none" strokeWidth={planSize.wall} variants={wallsBecome} />
      <motion.rect fill="var(--color-paper-raised)" strokeWidth={4} variants={becomes(planCounter.low, docks[0])} />
      <motion.rect fill="var(--color-paper-sunken)" strokeWidth={4} variants={becomes(planCounter.high, docks[1])} />
      <motion.rect fill="none" strokeWidth={3} variants={becomes(planCounter.backBar, docks[2])} />
      {shopPieces.map((piece, index) => (
        <motion.rect key={piece.key} fill="var(--color-paper-raised)" strokeWidth={4} variants={becomes(piece, racksInMorphOrder[index])} />
      ))}
    </g>
  );
}

export function PlannerSlide({ step }: SlideProps) {
  const phase: Phase = step === 0 ? "shop" : "warehouse";
  return (
    <div className="deck-gutter flex h-full flex-col justify-center gap-deck-hairline">
      <h2 className="font-display text-figure font-extrabold">
        <AnimatePresence mode="wait" initial={false}>
          <motion.span key={phase} className="block" initial="enter" animate="present" exit="exit">
            <MaskedLines lines={[phase === "shop" ? "Now" : "The future"]} delay={0.1} />
          </motion.span>
        </AnimatePresence>
      </h2>
      <svg
        viewBox={`${warehouseOutline.west - 40} -60 ${warehouseInnerWidth + 80} ${planSize.height + 120}`}
        className="min-h-0 w-full flex-1 overflow-visible"
        role="img"
        aria-label="A wheelchair follows the widest route through the shop, then the shop becomes a warehouse where robots follow planned routes"
      >
        <motion.g initial="enter" animate={phase}>
          <MorphingPlan />
          <WheelchairRoute phase={phase} />
          <RobotRoutes />
          {robots.map((robot) => (
            <RobotOnRoute key={`${robot.d}-${robot.offset}`} robot={robot} phase={phase} />
          ))}
        </motion.g>
      </svg>
    </div>
  );
}
