"use client";

import { AnimatePresence, motion, type Variants } from "motion/react";
import { AppCapture } from "../AppCapture";
import type { SlideProps } from "../slides";
import { easeDrawn, exitTransition } from "@/lib/motion";
import {
  formatInches,
  measurements,
  planCaseGap,
  planCases,
  planCounter,
  planDoor,
  planPerimeterPoints,
  planSeating,
  planSize,
  rectAttributes,
  shopDoorway,
  shopOutline,
  wallOutline,
  type PlanRect,
} from "../floorPlan";

const timeline = {
  dotsLand: 0.15,
  dotsSeconds: 1.2,
  linesDraw: 1.2,
  linesSeconds: 0.9,
  dimensions: 2.1,
  rows: 2.6,
  rowStagger: 0.28,
};

const DOT_SPACING = 17;
const ROW_X = 760;
const LEADER_BEND_X = 690;

const outlineRect: PlanRect = {
  key: "walls",
  x: shopOutline.west,
  y: shopOutline.north,
  width: shopOutline.east - shopOutline.west,
  height: shopOutline.south - shopOutline.north,
};

const counterRect: PlanRect = { ...planCounter.low, width: planCounter.low.width + planCounter.high.width };

function scatter(seed: number) {
  const value = Math.sin(seed * 12.9898) * 43758.5453;
  return value - Math.floor(value);
}

const scanDots = [outlineRect, counterRect, planCounter.backBar, ...planCases, ...planSeating]
  .flatMap((rect) => planPerimeterPoints(rect, DOT_SPACING))
  .map((point, index) => ({
    ...point,
    startX: point.x + (scatter(index) - 0.5) * 900,
    startY: point.y + (scatter(index + 0.5) - 0.5) * 700,
    delay: timeline.dotsLand + scatter(index + 0.25) * 0.5,
  }));

function dotLands(dot: (typeof scanDots)[number]): Variants {
  return {
    enter: { cx: dot.startX, cy: dot.startY, opacity: 0 },
    present: {
      cx: dot.x,
      cy: dot.y,
      opacity: [0, 1, 1, 0.3],
      transition: {
        cx: { duration: timeline.dotsSeconds, ease: easeDrawn, delay: dot.delay },
        cy: { duration: timeline.dotsSeconds, ease: easeDrawn, delay: dot.delay },
        opacity: { duration: timeline.linesDraw + timeline.linesSeconds - dot.delay, times: [0, 0.1, 0.75, 1], delay: dot.delay },
      },
    },
    exit: { opacity: 0, transition: exitTransition },
  };
}

function lineDraws(delay: number): Variants {
  return {
    enter: { pathLength: 0, opacity: 0 },
    present: {
      pathLength: 1,
      opacity: 1,
      transition: { pathLength: { duration: timeline.linesSeconds, ease: easeDrawn, delay }, opacity: { duration: 0.01, delay } },
    },
    exit: { opacity: 0, transition: exitTransition },
  };
}

function fadesIn(delay: number): Variants {
  return {
    enter: { opacity: 0 },
    present: { opacity: 1, transition: { duration: 0.5, delay } },
    exit: { opacity: 0, transition: exitTransition },
  };
}

function ScanDots() {
  return (
    <g fill="var(--color-ink)">
      {scanDots.map((dot, index) => (
        <motion.circle key={index} r={3.2} variants={dotLands(dot)} />
      ))}
    </g>
  );
}

function PlanLines() {
  const drawn = lineDraws(timeline.linesDraw);
  return (
    <g fill="none" stroke="var(--color-ink)" strokeLinejoin="round">
      <motion.path d={wallOutline(shopOutline, shopDoorway)} strokeWidth={planSize.wall} strokeLinecap="butt" variants={drawn} />
      <motion.rect {...rectAttributes(planCounter.backBar)} strokeWidth={3} variants={drawn} />
      <motion.rect {...rectAttributes(planCounter.low)} fill="var(--color-paper-raised)" strokeWidth={4} variants={drawn} />
      <motion.rect {...rectAttributes(planCounter.high)} fill="var(--color-paper-sunken)" strokeWidth={4} variants={drawn} />
      <motion.rect x={planCounter.reader.x - 10} y={planCounter.reader.y - 8} width={20} height={16} fill="var(--color-ink)" stroke="none" variants={fadesIn(timeline.linesDraw + 0.6)} />
      {planCases.map((rect) => (
        <motion.rect key={rect.key} {...rectAttributes(rect)} fill="var(--color-paper-raised)" strokeWidth={4} variants={drawn} />
      ))}
      {planSeating.map((seat) => (
        <motion.rect key={seat.key} {...rectAttributes(seat)} rx={seat.round ? seat.width / 2 : 4} fill="var(--color-paper)" stroke="var(--color-ink-muted)" strokeWidth={3} variants={drawn} />
      ))}
    </g>
  );
}

function Dimension({ fromX, toX, y, label, delay }: { fromX: number; toX: number; y: number; label: string; delay: number }) {
  return (
    <motion.g variants={fadesIn(delay)} stroke="var(--color-tape-deep)" strokeWidth={4}>
      <motion.line x1={fromX} x2={toX} y1={y} y2={y} variants={lineDraws(delay)} />
      <line x1={fromX} x2={fromX} y1={y - 14} y2={y + 14} />
      <line x1={toX} x2={toX} y1={y - 14} y2={y + 14} />
      <text x={(fromX + toX) / 2} y={y - 48} textAnchor="middle" stroke="none" fill="var(--color-ink)" className="font-display text-4xl font-bold figures-tabular">
        {label}
      </text>
    </motion.g>
  );
}

const firstSeat = planSeating[planSeating.length - 1];

const dataRows = [
  { name: "Ordering counter", value: `${formatInches(measurements.counterInches)} high`, anchor: { x: planCounter.high.x + planCounter.high.width / 2, y: planCounter.high.y + planCounter.high.height / 2 }, y: 170 },
  { name: "Display cases", value: `${formatInches(measurements.caseGapInches)} apart`, anchor: { x: planCaseGap.eastX + 60, y: planCaseGap.y }, y: 390 },
  { name: "Tables and chairs", value: "movable", anchor: { x: firstSeat.x + firstSeat.width / 2, y: firstSeat.y + firstSeat.height / 2 }, y: 590 },
  { name: "Front door", value: `${formatInches(measurements.doorInches)} wide`, anchor: { x: (planDoor.westX + planDoor.eastX) / 2, y: planDoor.y }, y: 770 },
];

function rowDelay(index: number) {
  return timeline.rows + index * timeline.rowStagger;
}

function Leader({ row, index }: { row: (typeof dataRows)[number]; index: number }) {
  return (
    <motion.path
      d={`M ${row.anchor.x} ${row.anchor.y} L ${LEADER_BEND_X} ${row.y} L ${ROW_X - 20} ${row.y}`}
      fill="none"
      stroke="var(--color-ink)"
      strokeWidth={2.5}
      variants={lineDraws(rowDelay(index))}
    />
  );
}

function DataRow({ row, index }: { row: (typeof dataRows)[number]; index: number }) {
  const delay = rowDelay(index);
  return (
    <g>
      <motion.circle cx={row.anchor.x} cy={row.anchor.y} r={8} fill="var(--color-tape)" stroke="var(--color-ink)" strokeWidth={2.5} variants={fadesIn(delay)} />
      <motion.g variants={fadesIn(delay + 0.3)}>
        <text x={ROW_X} y={row.y - 6} className="font-display text-5xl font-extrabold" fill="var(--color-ink)">
          {row.name}
        </text>
        <text x={ROW_X} y={row.y + 44} className="font-display text-4xl font-bold figures-tabular" fill="var(--color-ink-muted)">
          {row.value}
        </text>
      </motion.g>
    </g>
  );
}

export function ModelSlide({ step }: SlideProps) {
  return (
    <div className="deck-gutter relative flex h-full items-center justify-center">
      <AnimatePresence>
        {step > 0 && (
          <motion.div key="app" className="absolute inset-0 z-10 flex items-center justify-center bg-paper/80 px-deck-gap" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <div className="app-capture-wide">
              <AppCapture image="/deck/app-model.jpg" alt="The measured 3D model of the sample boba shop in the Standard Physics app, with its findings beside it" />
            </div>
          </motion.div>
        )}
      </AnimatePresence>
      <svg viewBox="-40 -60 1400 930" className="h-full max-h-deck-art w-full overflow-visible" role="img" aria-label="Scan points settle into a measured floor plan, and each object becomes a row of data">
        <ScanDots />
        {dataRows.map((row, index) => (
          <Leader key={row.name} row={row} index={index} />
        ))}
        <PlanLines />
        <Dimension fromX={planCaseGap.westX} toX={planCaseGap.eastX} y={planCaseGap.y} label={formatInches(measurements.caseGapInches)} delay={timeline.dimensions} />
        <Dimension fromX={planDoor.westX} toX={planDoor.eastX} y={planDoor.y - 36} label={formatInches(measurements.doorInches)} delay={timeline.dimensions + 0.2} />
        {dataRows.map((row, index) => (
          <DataRow key={row.name} row={row} index={index} />
        ))}
      </svg>
    </div>
  );
}
