"use client";

import { cubicBezier, motion, useTransform, type MotionValue, type Variants } from "motion/react";
import { easeDrawn, exitTransition } from "@/lib/motion";
import { facts } from "@/lib/facts";
import { CountFromProgress, FinePrint, MaskedLines, fadeReveal, useProgress } from "../primitives";

const pageCount = facts.adaStandardsPages.value;
const PAGE_THICKNESS = 2;
const STACK_BASE_Y = 690;
const STACK_SECONDS = 2.4;
const STACK_DELAY = 0.3;

const PAGE_HALF_WIDTH = 160;
const PAGE_HALF_DEPTH = 64;
const PAGE_LEFT = 20;
const pageFace = `M${PAGE_LEFT} 0 l${PAGE_HALF_WIDTH} ${-PAGE_HALF_DEPTH} l${PAGE_HALF_WIDTH} ${PAGE_HALF_DEPTH} l${-PAGE_HALF_WIDTH} ${PAGE_HALF_DEPTH} Z`;
const pageSurface = `matrix(${PAGE_HALF_WIDTH} ${PAGE_HALF_DEPTH} ${PAGE_HALF_WIDTH} ${-PAGE_HALF_DEPTH} ${PAGE_LEFT} 0)`;
const stackTopY = STACK_BASE_Y - (pageCount - 1) * PAGE_THICKNESS;
const stackHeight = STACK_BASE_Y - stackTopY;

function pageJitter(index: number) {
  return (((index * 37) % 9) - 4) * 0.7;
}

const stackEasing = cubicBezier(0.4, 0, 0.3, 1);
const LINEAR = [0, 0, 1, 1] as const;
const DROP_SECONDS = 0.45;
const LAND_LEAD_SECONDS = 0.24;

function secondsUntilStackReaches(fraction: number) {
  let low = 0;
  let high = 1;
  for (let step = 0; step < 24; step += 1) {
    const middle = (low + high) / 2;
    if (stackEasing(middle) < fraction) low = middle;
    else high = middle;
  }
  return high * STACK_SECONDS;
}

function pageLanding(index: number): Variants {
  const landsAt = STACK_DELAY + secondsUntilStackReaches(index / (pageCount - 1));
  const delay = Math.max(landsAt - LAND_LEAD_SECONDS, 0);
  return {
    enter: { y: -260, opacity: 0 },
    present: { y: 0, opacity: 1, transition: { y: { duration: DROP_SECONDS, ease: easeDrawn, delay }, opacity: { duration: 0.12, delay } } },
    exit: { opacity: 0, transition: { ...exitTransition, delay: (1 - index / pageCount) * 0.15 } },
  };
}

const textLines = Array.from({ length: 8 }, (_, line) => ({
  v: 0.22 + line * 0.075,
  end: line % 3 === 2 ? 0.58 : 0.86,
}));

function TopPageText() {
  return (
    <g transform={`translate(${pageJitter(pageCount - 1)} ${stackTopY}) ${pageSurface}`}>
      {textLines.map((line) => (
        <line
          key={line.v}
          x1={0.14}
          x2={line.end}
          y1={line.v}
          y2={line.v}
          stroke="var(--color-ink-faint)"
          strokeWidth={3}
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
        />
      ))}
    </g>
  );
}

const DIMENSION_X = PAGE_LEFT + PAGE_HALF_WIDTH * 2 + 28;
const DIMENSION_TICK = 12;

function HeightDimension({ progress }: { progress: MotionValue<number> }) {
  const top = useTransform(progress, (value) => STACK_BASE_Y - value * stackHeight);
  return (
    <g stroke="var(--color-ink)" strokeWidth={3}>
      <line x1={DIMENSION_X - DIMENSION_TICK} x2={DIMENSION_X + DIMENSION_TICK} y1={STACK_BASE_Y} y2={STACK_BASE_Y} />
      <motion.line x1={DIMENSION_X} x2={DIMENSION_X} y1={STACK_BASE_Y} y2={top} />
      <motion.line x1={DIMENSION_X - DIMENSION_TICK} x2={DIMENSION_X + DIMENSION_TICK} y1={top} y2={top} />
    </g>
  );
}

function PageStack({ progress }: { progress: MotionValue<number> }) {
  return (
    <svg viewBox="0 0 420 780" className="h-full w-auto overflow-visible" role="img" aria-label={`A stack of ${pageCount} pages`}>
      {Array.from({ length: pageCount }, (_, index) => (
        <g key={index} transform={`translate(${pageJitter(index)} ${STACK_BASE_Y - index * PAGE_THICKNESS})`}>
          <motion.path
            d={pageFace}
            fill="var(--color-paper-raised)"
            stroke="var(--color-ink-faint)"
            strokeWidth={0.7}
            variants={pageLanding(index)}
          />
        </g>
      ))}
      <motion.g variants={fadeReveal(STACK_DELAY + STACK_SECONDS * 0.9)}>
        <TopPageText />
      </motion.g>
      <HeightDimension progress={progress} />
    </svg>
  );
}

export function RulebookSlide() {
  const elapsed = useProgress(STACK_SECONDS, STACK_DELAY, LINEAR);
  const progress = useTransform(elapsed, stackEasing);

  return (
    <div className="deck-gutter grid h-full grid-cols-2 items-center gap-deck-gap">
      <div className="flex h-deck-art justify-center">
        <PageStack progress={progress} />
      </div>
      <div className="flex h-full flex-col justify-center">
        <p className="font-display text-display font-extrabold figures-tabular">
          <CountFromProgress progress={progress} total={pageCount} />
        </p>
        <p className="font-display text-lede font-bold">
          <MaskedLines lines={["pages in the ADA", "design standards"]} delay={0.6} />
        </p>
        <motion.div className="mt-auto" variants={fadeReveal(0)}>
          <FinePrint delay={1.6}>{facts.adaStandardsPages.source}</FinePrint>
        </motion.div>
      </div>
    </div>
  );
}
