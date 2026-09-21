"use client";

import { motion, type Variants } from "motion/react";
import { facts } from "@/lib/facts";
import { easeDrawn, easeSweep, exitTransition } from "@/lib/motion";

const INCHES_PER_MILE = 63_360;
const TRILLION = 1e12;

const stackMiles = (facts.boomerNetWorthTrillions.value * TRILLION * facts.dollarBillThicknessInches.value) / INCHES_PER_MILE;
export const moonTrips = stackMiles / facts.moonDistanceMiles.value;

const VIEW = { width: 800, height: 900 };
const EARTH = { cx: 400, cy: 1560, r: 760 };
const MOON = { cx: 690, cy: 110, r: 54 };
const ORBIT_Y = MOON.cy;
const STACKS = { left: 70, right: 610, width: 13 };

const wholeStacks = Math.floor(moonTrips);
const lastStackFraction = moonTrips - wholeStacks;
const stackCount = wholeStacks + (lastStackFraction > 0 ? 1 : 0);
const stackPitch = (STACKS.right - STACKS.left) / (stackCount - 1);

function earthSurfaceY(x: number) {
  return EARTH.cy - Math.sqrt(EARTH.r ** 2 - (x - EARTH.cx) ** 2);
}

const stacks = Array.from({ length: stackCount }, (_, index) => {
  const x = STACKS.left + index * stackPitch;
  const base = earthSurfaceY(x);
  const fraction = index === wholeStacks ? lastStackFraction : 1;
  return { x, base, top: base - (base - ORBIT_Y) * fraction };
});

export type MoonStacksTimeline = { closeUp: number; zoom: number; firstStack: number; moreStacks: number; stagger: number };

const storyTimeline: MoonStacksTimeline = { closeUp: 0.2, zoom: 1.4, firstStack: 1.9, moreStacks: 3.4, stagger: 0.06 };
const settledTimeline: MoonStacksTimeline = { closeUp: 0, zoom: 0, firstStack: 0, moreStacks: 0, stagger: 0 };

function billDrops(index: number, timeline: MoonStacksTimeline): Variants {
  const delay = timeline.closeUp + index * 0.08;
  return {
    enter: { y: -220, opacity: 0 },
    present: { y: 0, opacity: 1, transition: { y: { type: "spring", stiffness: 380, damping: 26, delay }, opacity: { duration: 0.1, delay } } },
  };
}

function closeUpZoomsOut(timeline: MoonStacksTimeline): Variants {
  return {
    enter: { scale: 1, opacity: 1 },
    present: { scale: 0.02, opacity: 0, transition: { scale: { duration: 0.7, ease: easeSweep, delay: timeline.zoom }, opacity: { duration: 0.3, delay: timeline.zoom + 0.4 } } },
  };
}

function spaceAppears(timeline: MoonStacksTimeline): Variants {
  return {
    enter: { opacity: 0 },
    present: { opacity: 1, transition: { duration: 0.6, delay: timeline.zoom + 0.3 } },
    exit: { opacity: 0, transition: exitTransition },
  };
}

function stackRises(index: number, timeline: MoonStacksTimeline): Variants {
  const isFirst = index === 0;
  const delay = isFirst ? timeline.firstStack : timeline.moreStacks + index * timeline.stagger;
  return {
    enter: { scaleY: 0 },
    present: { scaleY: 1, transition: { duration: isFirst ? 1.3 : 0.7, ease: easeDrawn, delay } },
  };
}

const BILL = { width: 300, height: 128 };

function DollarBill({ index, timeline }: { index: number; timeline: MoonStacksTimeline }) {
  const y = -index * 16;
  return (
    <motion.g variants={billDrops(index, timeline)}>
      <g transform={`translate(${-BILL.width / 2} ${y - BILL.height / 2})`}>
        <rect width={BILL.width} height={BILL.height} rx={6} fill="var(--color-paper-raised)" stroke="var(--color-ink)" strokeWidth={5} />
        <rect x={14} y={14} width={BILL.width - 28} height={BILL.height - 28} rx={3} fill="none" stroke="var(--color-ink-faint)" strokeWidth={3} />
        <ellipse cx={BILL.width / 2} cy={BILL.height / 2} rx={34} ry={40} fill="none" stroke="var(--color-ink)" strokeWidth={4} />
        <text x={BILL.width / 2} y={BILL.height / 2 + 14} textAnchor="middle" fill="var(--color-ink)" className="font-display text-4xl font-extrabold">
          1
        </text>
      </g>
    </motion.g>
  );
}

function CloseUp({ timeline }: { timeline: MoonStacksTimeline }) {
  const anchor = { x: stacks[0].x, y: stacks[0].base };
  return (
    <motion.g variants={closeUpZoomsOut(timeline)} style={{ originX: `${anchor.x}px`, originY: `${anchor.y}px` }}>
      <g transform={`translate(${VIEW.width / 2} ${VIEW.height / 2 + 140})`}>
        {Array.from({ length: 8 }, (_, index) => (
          <DollarBill key={index} index={index} timeline={timeline} />
        ))}
      </g>
    </motion.g>
  );
}

function Space({ timeline }: { timeline: MoonStacksTimeline }) {
  return (
    <motion.g variants={spaceAppears(timeline)}>
      <line x1={0} x2={VIEW.width} y1={ORBIT_Y} y2={ORBIT_Y} stroke="var(--color-ink-faint)" strokeWidth={3} strokeDasharray="10 12" />
      <circle cx={MOON.cx} cy={MOON.cy} r={MOON.r} fill="var(--color-paper-raised)" stroke="var(--color-ink)" strokeWidth={5} />
      <circle cx={MOON.cx - 16} cy={MOON.cy - 10} r={10} fill="var(--color-paper-sunken)" />
      <circle cx={MOON.cx + 18} cy={MOON.cy + 16} r={7} fill="var(--color-paper-sunken)" />
      {stacks.map((stack, index) => (
        <motion.rect
          key={stack.x}
          x={stack.x - STACKS.width / 2}
          y={stack.top}
          width={STACKS.width}
          height={stack.base - stack.top}
          fill={index === 0 ? "var(--color-tape)" : "var(--color-ink)"}
          style={{ originY: 1 }}
          variants={stackRises(index, timeline)}
        />
      ))}
      <circle cx={EARTH.cx} cy={EARTH.cy} r={EARTH.r} fill="var(--color-paper-sunken)" stroke="var(--color-ink)" strokeWidth={5} />
      <text x={MOON.cx - MOON.r - 16} y={ORBIT_Y - 22} textAnchor="end" fill="var(--color-ink-muted)" className="font-display text-4xl font-bold figures-tabular">
        {`${new Intl.NumberFormat("en-US").format(facts.moonDistanceMiles.value)} miles`}
      </text>
    </motion.g>
  );
}

export function MoonStacks({ settled = false }: { settled?: boolean }) {
  const timeline = settled ? settledTimeline : storyTimeline;
  return (
    <svg
      viewBox={`0 0 ${VIEW.width} ${VIEW.height}`}
      className="size-full overflow-hidden"
      role="img"
      aria-label={`A stack of $1 bills worth $${facts.boomerNetWorthTrillions.value} trillion reaches the Moon ${Math.floor(moonTrips)} times`}
    >
      <Space timeline={timeline} />
      {!settled && <CloseUp timeline={timeline} />}
    </svg>
  );
}
