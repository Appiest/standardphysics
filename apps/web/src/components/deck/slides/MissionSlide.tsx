"use client";

import { motion, type Variants } from "motion/react";
import { easeDrawn, easeSweep, exitTransition } from "@/lib/motion";
import { salesCountersExcerpt } from "@/lib/facts";

const [beforeHighlight, highlighted, afterHighlight] = salesCountersExcerpt.split("|");
const missionLines = ["That’s why we built", "Standard Physics."];

const legalWall: Variants = {
  enter: { opacity: 0, y: "0vh" },
  present: {
    opacity: [0, 1, 1, 0.16],
    y: "-8vh",
    transition: {
      opacity: { duration: 3.2, times: [0, 0.12, 0.5, 1], ease: "linear" },
      y: { duration: 9, ease: "linear" },
    },
  },
  exit: { opacity: 0, transition: exitTransition },
};

const highlighter: Variants = {
  enter: { scaleX: 0 },
  present: { scaleX: 1, transition: { duration: 0.7, ease: easeSweep, delay: 0.7 } },
};

const makeRoom: Variants = {
  enter: { fontStretch: "75%", opacity: 0, y: 40 },
  present: {
    fontStretch: "100%",
    opacity: 1,
    y: 0,
    transition: {
      opacity: { duration: 0.4, delay: 1.3 },
      y: { duration: 0.9, ease: easeDrawn, delay: 1.3 },
      fontStretch: { duration: 1.3, ease: easeDrawn, delay: 2.1 },
    },
  },
  exit: { opacity: 0, transition: exitTransition },
};

function LegalWall() {
  return (
    <motion.p
      aria-hidden
      variants={legalWall}
      className="deck-gutter absolute inset-0 overflow-hidden text-justify font-display text-caption text-ink-muted"
    >
      {beforeHighlight}
      <span className="relative whitespace-nowrap text-ink">
        <motion.span
          variants={highlighter}
          className="highlight-mark origin-left"
        />
        {highlighted}
      </span>
      {afterHighlight} {beforeHighlight}
    </motion.p>
  );
}

export function MissionSlide() {
  return (
    <div className="relative h-full">
      <LegalWall />
      <div className="deck-gutter relative flex h-full items-center justify-center">
        <motion.h2 variants={makeRoom} className="text-center font-display text-headline font-extrabold">
          {missionLines.map((line) => (
            <span key={line} className="block whitespace-nowrap">
              {line}
            </span>
          ))}
        </motion.h2>
      </div>
    </div>
  );
}
