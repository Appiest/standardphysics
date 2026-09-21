"use client";

import { AnimatePresence, animate, motion, useMotionValue, useTransform, type MotionValue, type Variants } from "motion/react";
import { useEffect } from "react";
import { easeDrawn, exitTransition } from "@/lib/motion";
import { FinePrint, MaskedLines } from "../primitives";
import type { SlideProps } from "../slides";

type ToolsPhase = "runs" | "aria" | "marimo";
const toolsPhases: ToolsPhase[] = ["runs", "aria", "marimo"];

type Run = { label: string; cellMillimeters: number | null; candidates: number; weakest: number };

const runs: Run[] = [
  { label: "20 mm", cellMillimeters: 20, candidates: 4, weakest: 0.4444 },
  { label: "20 mm", cellMillimeters: 20, candidates: 16, weakest: 0.7778 },
  { label: "25 mm", cellMillimeters: 25, candidates: 4, weakest: 1 },
  { label: "25 mm", cellMillimeters: 25, candidates: 16, weakest: 1 },
  { label: "30 mm", cellMillimeters: 30, candidates: 4, weakest: 0.8889 },
  { label: "30 mm", cellMillimeters: 30, candidates: 16, weakest: 0.9861 },
  { label: "35 mm", cellMillimeters: 35, candidates: 4, weakest: 0.8889 },
  { label: "35 mm", cellMillimeters: 35, candidates: 16, weakest: 0.8889 },
  { label: "40 mm", cellMillimeters: 40, candidates: 4, weakest: 0.8889 },
  { label: "40 mm", cellMillimeters: 40, candidates: 16, weakest: 0.8889 },
  { label: "Stand-in", cellMillimeters: null, candidates: 4, weakest: 0.3796 },
];

const ariaRuns = new Set([0, 1]);
const percent = (value: number) => `${Math.round(value * 100)}%`;

function tileAppears(index: number): Variants {
  return {
    enter: { opacity: 0, y: 20 },
    present: { opacity: 1, y: 0, transition: { duration: 0.45, ease: easeDrawn, delay: 0.2 + index * 0.06 } },
    exit: { opacity: 0, transition: exitTransition },
  };
}

function RunTile({ run, index, phase }: { run: Run; index: number; phase: ToolsPhase }) {
  const focused = phase === "aria" && ariaRuns.has(index);
  const dimmed = phase === "aria" && !focused;
  return (
    <motion.div
      variants={tileAppears(index)}
      animate={{ opacity: dimmed ? 0.25 : 1, scale: focused ? 1.06 : 1 }}
      transition={{ duration: 0.5, ease: easeDrawn }}
      className={`flex flex-col gap-deck-hairline p-deck-hairline shadow-md ${focused ? "bg-ink text-paper" : "bg-paper-raised text-ink"}`}
    >
      <p className="font-display text-caption font-bold">
        {run.label}, {run.candidates} options
      </p>
      <div className="h-deck-hairline w-full bg-paper-sunken">
        <motion.div
          className={`h-full origin-left ${focused ? "bg-tape" : "bg-ink"}`}
          initial={{ scaleX: 0 }}
          animate={{ scaleX: run.weakest }}
          transition={{ duration: 0.8, ease: easeDrawn, delay: 0.5 + index * 0.06 }}
        />
      </div>
      <p className="font-display text-lede font-extrabold figures-tabular">{percent(run.weakest)}</p>
    </motion.div>
  );
}

function RunGrid({ phase }: { phase: ToolsPhase }) {
  return (
    <div role="img" aria-label="Eleven W&B runs, one per configuration, with the weakest score of each" className="grid grid-cols-4 gap-deck-hairline">
      {runs.map((run, index) => (
        <RunTile key={`${run.label}-${run.candidates}`} run={run} index={index} phase={phase} />
      ))}
    </div>
  );
}

type Knob = { name: string; from: number; to: number; min: number; max: number; limit: number; passesAbove: boolean };

const knobs: Knob[] = [
  { name: "Gap between the display cases", from: 31, to: 38, min: 24, max: 72, limit: 36, passesAbove: true },
  { name: "Counter height", from: 47, to: 34, min: 28, max: 52, limit: 36, passesAbove: false },
];

function useSweep(knob: Knob, delay: number) {
  const value = useMotionValue(knob.from);
  useEffect(() => {
    const controls = animate(value, [knob.from, knob.to, knob.to, knob.from], {
      duration: 5,
      times: [0, 0.4, 0.7, 1],
      ease: "easeInOut",
      delay,
      repeat: Infinity,
      repeatDelay: 0.6,
    });
    return () => controls.stop();
  }, [value, knob, delay]);
  return value;
}

function passes(knob: Knob, inches: number) {
  return knob.passesAbove ? inches >= knob.limit : inches <= knob.limit;
}

function KnobRow({ knob, delay }: { knob: Knob; delay: number }) {
  const inches = useSweep(knob, delay);
  const thumb = useTransform(inches, (value) => `${((value - knob.min) / (knob.max - knob.min)) * 100}%`);
  const readout = useTransform(inches, (value) => `${(Math.round(value * 2) / 2).toFixed(1)} in`);
  const verdict = useTransform<number, string>(inches, (value) => (passes(knob, value) ? "Passes" : "Fails"));
  const verdictColor = useTransform(inches, (value) => (passes(knob, value) ? "var(--color-pass)" : "var(--color-fail)"));
  return (
    <div className="flex flex-col gap-deck-hairline">
      <div className="flex items-baseline justify-between gap-deck-gap font-display text-caption font-bold">
        <span>{knob.name}</span>
        <motion.span className="whitespace-nowrap figures-tabular">{readout}</motion.span>
      </div>
      <div className="relative h-deck-hairline w-full rounded-full bg-paper-sunken">
        <SliderThumb position={thumb} />
      </div>
      <motion.p className="font-display text-caption font-extrabold" style={{ color: verdictColor }}>
        {verdict}
      </motion.p>
    </div>
  );
}

function SliderThumb({ position }: { position: MotionValue<string> }) {
  return <motion.span aria-hidden className="slider-thumb absolute top-1/2 rounded-full bg-ink shadow-md" style={{ left: position }} />;
}

function MarimoPanel() {
  return (
    <motion.div
      key="marimo"
      initial="enter"
      animate="present"
      exit="exit"
      variants={tileAppears(0)}
      role="img"
      aria-label="Two marimo sliders sweep the aisle gap and counter height while their checks flip between passing and failing"
      className="flex flex-col gap-deck-rise bg-paper-raised p-deck-gap shadow-xl"
    >
      {knobs.map((knob, index) => (
        <KnobRow key={knob.name} knob={knob} delay={0.6 + index * 0.4} />
      ))}
    </motion.div>
  );
}

const copy: Record<ToolsPhase, { headline: string[]; detail: string[]; source: string }> = {
  runs: {
    headline: ["Every", "experiment is", "a W&B run."],
    detail: ["Each run scored on 39 labeled cases."],
    source: "Standard Physics evaluation grid, docs/aria.md",
  },
  aria: {
    headline: ["ARIA found", "where the fix", "agent needed", "more options."],
    detail: [],
    source: "fix_resolves_finding, Standard Physics evaluation grid, docs/aria.md",
  },
  marimo: {
    headline: ["Drag a slider", "in marimo, and", "the checks", "react live."],
    detail: [],
    source: "notebooks/scenario_sweep.py, docs/marimo.md",
  },
};

function ToolsCopy({ phase }: { phase: ToolsPhase }) {
  return (
    <>
      <h2 className="font-display text-figure font-extrabold">
        <MaskedLines lines={copy[phase].headline} delay={0.1} />
      </h2>
      {copy[phase].detail.length > 0 && (
        <p className="mt-deck-rise font-display text-caption font-bold text-ink-muted">
          <MaskedLines lines={copy[phase].detail} delay={0.4} />
        </p>
      )}
      <div className="mt-deck-rise">
        <FinePrint delay={0.8}>{copy[phase].source}</FinePrint>
      </div>
    </>
  );
}

export function ToolsSlide({ step }: SlideProps) {
  const phase = toolsPhases[Math.min(step, toolsPhases.length - 1)];
  return (
    <div className="deck-gutter grid h-full grid-cols-[minmax(0,1fr)_minmax(0,1.35fr)] items-center gap-deck-gap">
      <div>
        <AnimatePresence mode="wait" initial={false}>
          <motion.div key={phase} initial="enter" animate="present" exit="exit">
            <ToolsCopy phase={phase} />
          </motion.div>
        </AnimatePresence>
      </div>
      <div className="relative">
        <AnimatePresence mode="wait" initial={false}>
          {phase === "marimo" ? (
            <MarimoPanel key="marimo" />
          ) : (
            <motion.div key="runs" initial="enter" animate="present" exit="exit">
              <RunGrid phase={phase} />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
