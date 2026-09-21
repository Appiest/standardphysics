"use client";

import { motion, type Variants } from "motion/react";
import { easeDrawn, exitTransition } from "@/lib/motion";

const CUP_OUTLINE = "M85.2 225 L122 566 Q124 584 142 584 L258 584 Q276 584 278 566 L314.8 225";
const LID_DOME = "M92 204 C96 118 304 118 308 204";
const TEA_FILL = "M92 300 L122 566 Q124 584 142 584 L258 584 Q276 584 278 566 L308 300 Z";

const pearlRows = [
  { y: 556, xs: [150, 186, 222, 258] },
  { y: 522, xs: [138, 172, 206, 240, 272] },
  { y: 490, xs: [152, 188, 224, 260] },
];

const pearls = pearlRows.flatMap((row, rowIndex) =>
  row.xs.map((x, column) => ({ x, y: row.y + ((column * 7) % 5), order: rowIndex * 5 + ((column * 3) % 5) })),
);

const buildTimeline = {
  outline: { delay: 0.15, duration: 0.5 },
  tea: { delay: 0.45, duration: 0.6 },
  pearls: { delay: 0.65, stagger: 0.025 },
  lid: 1.15,
  straw: 1.35,
};

const stroke = { stroke: "var(--color-ink)", strokeWidth: 7, strokeLinecap: "round", strokeLinejoin: "round", fill: "none" } as const;

function drawn(delay: number, duration: number): Variants {
  return {
    enter: { pathLength: 0, opacity: 0 },
    present: { pathLength: 1, opacity: 1, transition: { pathLength: { duration, ease: easeDrawn, delay }, opacity: { duration: 0.01, delay } } },
    exit: { opacity: 0, transition: exitTransition },
  };
}

function pearlDrop(order: number): Variants {
  const delay = buildTimeline.pearls.delay + order * buildTimeline.pearls.stagger;
  return {
    enter: { y: -520, opacity: 0 },
    present: {
      y: 0,
      opacity: 1,
      transition: { y: { type: "spring", stiffness: 380, damping: 18, delay }, opacity: { duration: 0.01, delay } },
    },
    exit: { opacity: 0, transition: exitTransition },
  };
}

const teaRise: Variants = {
  enter: { scaleY: 0 },
  present: { scaleY: 1, transition: { ...buildTimeline.tea, ease: easeDrawn } },
  exit: { opacity: 0, transition: exitTransition },
};

const lidDrop: Variants = {
  enter: { y: -140, opacity: 0 },
  present: { y: 0, opacity: 1, transition: { type: "spring", stiffness: 420, damping: 22, delay: buildTimeline.lid } },
  exit: { opacity: 0, transition: exitTransition },
};

const strawStab: Variants = {
  enter: { y: -260, opacity: 0 },
  present: { y: 0, opacity: 1, transition: { type: "spring", stiffness: 360, damping: 24, delay: buildTimeline.straw } },
  exit: { opacity: 0, transition: exitTransition },
};

export function BobaCup({ className = "h-full max-h-full w-auto" }: { className?: string }) {
  return (
    <svg viewBox="0 0 400 620" className={`${className} overflow-visible`} role="img" aria-label="A boba tea cup filling with tapioca pearls">
      <defs>
        <clipPath id="cup-inside">
          <path d={TEA_FILL} />
        </clipPath>
      </defs>
      <motion.path d={TEA_FILL} fill="var(--color-paper-sunken)" style={{ transformOrigin: "200px 584px" }} variants={teaRise} />
      <g clipPath="url(#cup-inside)">
        {pearls.map((pearl) => (
          <motion.circle key={`${pearl.x}-${pearl.y}`} cx={pearl.x} cy={pearl.y} r={17} fill="var(--color-ink)" variants={pearlDrop(pearl.order)} />
        ))}
      </g>
      <motion.g variants={strawStab}>
        <path d="M214 36 L246 36 L222 540 L196 540 Z" {...stroke} fill="var(--color-paper-raised)" />
      </motion.g>
      <motion.path d={CUP_OUTLINE} {...stroke} variants={drawn(buildTimeline.outline.delay, buildTimeline.outline.duration)} />
      <motion.g variants={lidDrop}>
        <path d={LID_DOME} {...stroke} fill="var(--color-paper-raised)" fillOpacity={0.55} />
        <rect x={70} y={200} width={260} height={22} rx={11} {...stroke} fill="var(--color-paper-raised)" />
      </motion.g>
    </svg>
  );
}
