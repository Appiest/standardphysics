"use client";

import { motion } from "motion/react";
import { easeDrawn } from "@/lib/motion";
import { finetuneRuns, loopResults, promotedRl, ruleShifts, type FinetuneRun, type RuleShift } from "../finetuneLedger";
import { fadeReveal } from "../primitives";

const WIDTH = 1120;
const TRUNK_Y = 380;
const TALLEST = 300;
const COLUMN = WIDTH / finetuneRuns.length;
const mostTokens = Math.max(...finetuneRuns.map((run) => run.spend.train_tokens));

function wrapWords(text: string, width = 11) {
  return text.split(" ").reduce<string[]>((lines, word) => {
    const last = lines.at(-1);
    if (last !== undefined && last.length + word.length + 1 <= width) return [...lines.slice(0, -1), `${last} ${word}`];
    return [...lines, word];
  }, []);
}

function growIn(delay: number) {
  return {
    initial: { pathLength: 0 },
    animate: { pathLength: 1 },
    transition: { duration: 0.9, ease: easeDrawn, delay },
  };
}

function placeIn(delay: number) {
  return {
    initial: { scale: 0, opacity: 0 },
    animate: { scale: 1, opacity: 1 },
    transition: { duration: 0.45, ease: easeDrawn, delay },
    style: { transformBox: "fill-box" as const, transformOrigin: "center" },
  };
}

type StemGeometry = { x: number; top: number; sftTop: number; hasSft: boolean; hasRl: boolean; delay: number };

/** A stem is as tall as the tokens its run trained on; with both stages, SFT takes the lower half and RL the upper. */
function stemGeometry(run: FinetuneRun, index: number): StemGeometry {
  const height = (run.spend.train_tokens / mostTokens) * TALLEST;
  const hasSft = Boolean(run.training.sft_rows);
  const hasRl = Boolean(run.training.rl_steps_done);
  const sftShare = hasRl ? 0.5 : 1;
  return {
    x: COLUMN * (index + 0.5),
    top: TRUNK_Y - height,
    sftTop: hasSft ? TRUNK_Y - height * sftShare : TRUNK_Y,
    hasSft,
    hasRl,
    delay: 0.3 + index * 0.08,
  };
}

function StemMarks({ run, geometry }: { run: FinetuneRun; geometry: StemGeometry }) {
  const { x, top, sftTop, hasSft, hasRl, delay } = geometry;
  const rlNode = promotedRl(run) ? "fill-tape stroke-ink" : "fill-paper-raised stroke-tape-deep";
  return (
    <>
      {hasSft && <motion.line x1={x} x2={x} y1={TRUNK_Y} y2={sftTop} className="stroke-ink" strokeWidth={9} strokeLinecap="round" {...growIn(delay)} />}
      {hasRl && <motion.line x1={x} x2={x} y1={sftTop} y2={top} className="stroke-tape-deep" strokeWidth={9} strokeLinecap="round" {...growIn(delay + 0.3)} />}
      {hasSft && <motion.circle cx={x} cy={sftTop} r={13} className="fill-ink" {...placeIn(delay + 0.5)} />}
      {hasRl && <motion.circle cx={x} cy={top} r={13} strokeWidth={4} className={rlNode} {...placeIn(delay + 0.8)} />}
    </>
  );
}

function Stem({ run, index }: { run: FinetuneRun; index: number }) {
  const geometry = stemGeometry(run, index);
  return (
    <g>
      <StemMarks run={run} geometry={geometry} />
      <text x={geometry.x} y={geometry.top - 28} textAnchor="middle" className="fill-ink-muted font-display figures-tabular" fontSize={22}>
        {(run.spend.train_tokens / 1_000_000).toFixed(1)}M
      </text>
      <text x={geometry.x} y={TRUNK_Y + 44} textAnchor="middle" className="fill-ink font-display font-bold" fontSize={18}>
        {wrapWords(run.title).map((line, lineIndex) => (
          <tspan key={line} x={geometry.x} dy={lineIndex === 0 ? 0 : 22}>
            {line}
          </tspan>
        ))}
      </text>
    </g>
  );
}

export function LineagePanel() {
  return (
    <motion.div initial="enter" animate="present" exit="exit" variants={fadeReveal(0.1, 20)} className="bg-paper-raised p-deck-gap shadow-xl">
      <svg
        viewBox={`0 0 ${WIDTH} 520`}
        className="w-full overflow-visible"
        role="img"
        aria-label={`${finetuneRuns.length} fine-tuning runs of Qwen 3.8 27B, each a stem as tall as the tokens it trained on, dark for SFT and amber for RL`}
      >
        <line x1={0} x2={WIDTH} y1={TRUNK_Y} y2={TRUNK_Y} className="stroke-ink" strokeWidth={12} strokeLinecap="round" />
        {finetuneRuns.map((run, index) => (
          <Stem key={run.key} run={run} index={index} />
        ))}
      </svg>
    </motion.div>
  );
}

const wholePercent = (share: number) => `${Math.round(share * 100)}%`;

function RuleShiftRow({ shift, index }: { shift: RuleShift; index: number }) {
  const rose = shift.after > shift.before;
  const left = Math.min(shift.before, shift.after);
  const span = Math.abs(shift.after - shift.before);
  return (
    <div role="row" className="contents">
      <motion.span role="rowheader" variants={fadeReveal(0.3 + index * 0.07, 12)} className="font-display text-caption font-bold">
        {shift.run.title}
      </motion.span>
      <motion.span role="cell" variants={fadeReveal(0.35 + index * 0.07, 12)} className="relative mx-deck-hairline block h-deck-hairline rounded-full bg-paper-sunken">
        <motion.span
          className={`absolute inset-y-0 block origin-left rounded-full ${rose ? "bg-pass" : "bg-ink-muted"}`}
          style={{ left: `${left * 100}%`, width: `${span * 100}%` }}
          initial={{ scaleX: 0 }}
          animate={{ scaleX: 1 }}
          transition={{ duration: 0.8, ease: easeDrawn, delay: 0.6 + index * 0.07 }}
        />
        <span aria-hidden className="absolute top-1/2 size-deck-hairline -translate-x-1/2 -translate-y-1/2 rounded-full bg-paper-raised ring-4 ring-ink" style={{ left: `${shift.before * 100}%` }} />
        <span aria-hidden className={`absolute top-1/2 size-deck-hairline -translate-x-1/2 -translate-y-1/2 rounded-full ${rose ? "bg-pass" : "bg-ink-muted"}`} style={{ left: `${shift.after * 100}%` }} />
      </motion.span>
      <motion.span role="cell" variants={fadeReveal(0.4 + index * 0.07, 12)} className="whitespace-nowrap text-right font-display text-caption font-bold figures-tabular">
        {wholePercent(shift.before)} → {wholePercent(shift.after)}
      </motion.span>
    </div>
  );
}

export function RulesLearnedPanel() {
  return (
    <motion.div
      initial="enter"
      animate="present"
      exit="exit"
      variants={fadeReveal(0.1, 20)}
      role="table"
      aria-label="Share of held-out answers that broke no hard rule, before training and after each run's last stage"
      className="grid grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)_auto] items-center gap-x-deck-hairline gap-y-deck-hairline bg-paper-raised p-deck-gap shadow-xl"
    >
      {ruleShifts.map((shift, index) => (
        <RuleShiftRow key={shift.run.key} shift={shift} index={index} />
      ))}
    </motion.div>
  );
}

export function SolvedPanel() {
  const best = Math.max(...loopResults.map((result) => result.cleared));
  return (
    <motion.div
      initial="enter"
      animate="present"
      exit="exit"
      variants={fadeReveal(0.1, 20)}
      role="img"
      aria-label={loopResults.map((result) => `${result.label}: ${result.cleared} of ${result.of} rooms cleared`).join("; ")}
      className="flex flex-col gap-deck-rise bg-paper-raised p-deck-gap shadow-xl"
    >
      {loopResults.map((result, index) => (
        <motion.div key={result.label} variants={fadeReveal(0.3 + index * 0.12, 12)} className="flex flex-col gap-deck-hairline">
          <div className="flex items-baseline justify-between gap-deck-gap font-display text-caption font-bold">
            <span>{result.label}</span>
            <span className="whitespace-nowrap figures-tabular">
              {result.cleared} of {result.of}
            </span>
          </div>
          <div className="h-deck-hairline w-full rounded-full bg-paper-sunken">
            <motion.div
              className={`h-full origin-left rounded-full ${result.cleared === best ? "bg-ink" : "bg-ink-muted"}`}
              initial={{ scaleX: 0 }}
              animate={{ scaleX: result.cleared / result.of }}
              transition={{ duration: 0.9, ease: easeDrawn, delay: 0.5 + index * 0.12 }}
            />
          </div>
        </motion.div>
      ))}
    </motion.div>
  );
}
