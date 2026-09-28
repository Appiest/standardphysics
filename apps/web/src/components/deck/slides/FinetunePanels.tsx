"use client";

import { motion } from "motion/react";
import { easeDrawn } from "@/lib/motion";
import { benchmarkedRuns, clearedChanges, finetuneRuns, promotedRl, type FinetuneRun, type StageChange } from "../finetuneLedger";
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

function chipTone(points: number) {
  if (points > 0.05) return "bg-pass/15 text-pass";
  return points < -0.05 ? "bg-fail/12 text-fail" : "bg-paper-sunken text-ink-muted";
}

function signedPoints(points: number) {
  const size = Math.abs(points).toFixed(1);
  return points < -0.05 ? `−${size}` : `+${size}`;
}

function ChangeChip({ change }: { change: StageChange }) {
  return (
    <span className={`whitespace-nowrap rounded-full px-deck-hairline py-1 font-display text-caption font-bold figures-tabular ${chipTone(change.points)}`}>
      {change.stage} {signedPoints(change.points)}
    </span>
  );
}

export function BenchmarkPanel() {
  return (
    <motion.div
      initial="enter"
      animate="present"
      exit="exit"
      variants={fadeReveal(0.1, 20)}
      role="table"
      aria-label="Change in held-out rooms cleared after each training stage, in percentage points"
      className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-deck-gap gap-y-deck-hairline bg-paper-raised p-deck-gap shadow-xl"
    >
      {benchmarkedRuns.map((run, index) => (
        <div key={run.key} role="row" className="contents">
          <motion.span role="rowheader" variants={fadeReveal(0.3 + index * 0.07, 12)} className="font-display text-caption font-bold">
            {run.title}
          </motion.span>
          <motion.span role="cell" variants={fadeReveal(0.35 + index * 0.07, 12)} className="flex justify-end gap-deck-hairline">
            {clearedChanges(run).map((change) => (
              <ChangeChip key={change.stage} change={change} />
            ))}
          </motion.span>
        </div>
      ))}
    </motion.div>
  );
}
