"use client";

import { Check } from "@phosphor-icons/react";
import { animate, motion, useMotionValue, useTransform, type MotionValue } from "motion/react";
import { useEffect } from "react";
import { finetuneTotals } from "../finetuneLedger";
import { fadeReveal } from "../primitives";

/** The hard rules in packages/agents/standardphysics_agents/fix/constraints.py, which both the notebook and the training reward call. */
const hardRules = ["collided", "left_the_floor", "blocked_a_door", "moved_something_fixed", "resized", "inventory_changed"];

const BREAKS_AT = 0.72;

function usePush() {
  const push = useMotionValue(0);
  useEffect(() => {
    const controls = animate(push, [0, 1, 1, 0], { duration: 5, times: [0, 0.45, 0.75, 1], ease: "easeInOut", delay: 0.6, repeat: Infinity, repeatDelay: 0.6 });
    return () => controls.stop();
  }, [push]);
  return push;
}

function CollidedChip({ push }: { push: MotionValue<number> }) {
  const background = useTransform(push, (value) => (value > BREAKS_AT ? "var(--color-fail)" : "var(--color-paper-sunken)"));
  const color = useTransform(push, (value) => (value > BREAKS_AT ? "var(--color-paper-raised)" : "var(--color-ink)"));
  return (
    <motion.span className="whitespace-nowrap px-deck-hairline py-1 font-mono text-fineprint" style={{ background, color }}>
      collided
    </motion.span>
  );
}

export function RulesInMarimoPanel() {
  const push = usePush();
  const thumb = useTransform(push, (value) => `${value * 100}%`);
  const verdict = useTransform<number, string>(push, (value) => (value > BREAKS_AT ? "Breaks collided" : "Allowed"));
  const verdictColor = useTransform(push, (value) => (value > BREAKS_AT ? "var(--color-fail)" : "var(--color-pass)"));
  return (
    <motion.div
      initial="enter"
      animate="present"
      exit="exit"
      variants={fadeReveal(0.1, 20)}
      role="img"
      aria-label="A marimo slider pushes a sofa across a scanned room until the hard-rule checker reports that it collided"
      className="flex flex-col gap-deck-rise bg-paper-raised p-deck-gap shadow-xl"
    >
      <div className="flex flex-col gap-deck-hairline">
        <div className="flex items-baseline justify-between gap-deck-gap font-display text-caption font-bold">
          <span>Move the sofa, up to 24 in</span>
          <motion.span style={{ color: verdictColor }}>{verdict}</motion.span>
        </div>
        <div className="relative h-deck-hairline w-full rounded-full bg-paper-sunken">
          <motion.span aria-hidden className="slider-thumb absolute top-1/2 rounded-full bg-ink shadow-md" style={{ left: thumb }} />
        </div>
      </div>
      <div className="flex flex-wrap gap-deck-hairline">
        <CollidedChip push={push} />
        {hardRules.slice(1).map((rule) => (
          <span key={rule} className="whitespace-nowrap bg-paper-sunken px-deck-hairline py-1 font-mono text-fineprint">
            {rule}
          </span>
        ))}
      </div>
    </motion.div>
  );
}

type Stage = { name: string; detail: string; built: boolean };

const stages: Stage[] = [
  { name: "Check the rules", detail: "Sliders rebuild a shop and the real evaluator re-scores it.", built: true },
  { name: "Nudge a real scan", detail: "The checker that grades the model names the rule a move breaks.", built: true },
  { name: "Grade every checkpoint", detail: `${finetuneTotals.graded.toLocaleString("en-US")} graded answers behind one benchmark picker.`, built: true },
  { name: "Rate layouts", detail: "Pick the better of two plans in marimo, feeding the reward.", built: false },
  { name: "Watch RL live", detail: "Plot reward each step and stop a run that has nothing to learn.", built: false },
  { name: "Pick the checkpoint", detail: "Rank adapters by five-try score, not single tries.", built: false },
];

function StageCard({ stage, index }: { stage: Stage; index: number }) {
  const surface = stage.built ? "bg-paper-raised shadow-md" : "border-2 border-dashed border-ink-faint";
  return (
    <motion.div variants={fadeReveal(0.25 + index * 0.08, 16)} className={`flex flex-col gap-2 p-deck-hairline ${surface}`}>
      <div className="flex items-center justify-between gap-deck-hairline font-display text-caption font-bold">
        <span>{stage.name}</span>
        {stage.built ? (
          <Check aria-label="Built" weight="bold" className="size-deck-hairline shrink-0 text-pass" />
        ) : (
          <span className="font-display text-fineprint font-bold text-ink-muted">Next</span>
        )}
      </div>
      <p className={`font-display text-fineprint ${stage.built ? "text-ink" : "text-ink-muted"}`}>{stage.detail}</p>
    </motion.div>
  );
}

export function MarimoMapPanel() {
  return (
    <motion.div
      initial="enter"
      animate="present"
      exit="exit"
      variants={fadeReveal(0.1, 20)}
      role="list"
      aria-label="Six steps of the training loop: three built in marimo, three marimo could take on next"
      className="grid grid-cols-2 gap-deck-hairline"
    >
      {stages.map((stage, index) => (
        <div key={stage.name} role="listitem" className="contents">
          <StageCard stage={stage} index={index} />
        </div>
      ))}
    </motion.div>
  );
}
