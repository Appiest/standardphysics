"use client";

import { motion, type Variants } from "motion/react";
import { facts } from "@/lib/facts";
import { exitTransition } from "@/lib/motion";
import { CountFromProgress, FinePrint, MaskedLines, useProgress } from "./primitives";

const MILLISECONDS_PER_DAY = 86_400_000;
const PRINT_SECONDS = 1.8;
const PRINT_DELAY = 0.35;
const PRINTER_STEPS = 14;

const dollars = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });

function daysBetween(start: string, end: string) {
  return Math.round((Date.parse(end) - Date.parse(start)) / MILLISECONDS_PER_DAY);
}

type ReceiptRow = { label: string; value: string; emphasis?: boolean };

const receiptRows: ReceiptRow[] = [
  { label: "Visit", value: facts.lawsuit.visitMonth },
  { label: "Counter height", value: `${facts.counterHeightInLawsuit.value} in` },
  { label: "Minimum damages", value: dollars.format(facts.californiaMinimumDamages.value), emphasis: true },
  { label: "Legal fees and costs", value: "Extra" },
  { label: "Days until closed", value: String(daysBetween(facts.lawsuit.filedOn, facts.lawsuit.closedOn)) },
];

const printerStep = (progress: number) => Math.floor(progress * PRINTER_STEPS) / PRINTER_STEPS;

const paperPrinting: Variants = {
  enter: { y: "-100%" },
  present: { y: "0%", transition: { duration: PRINT_SECONDS, delay: PRINT_DELAY, ease: printerStep } },
  exit: { opacity: 0, transition: exitTransition },
};

export function Receipt() {
  return (
    <div className="flex w-receipt min-w-fit flex-col items-stretch text-caption receipt-shadow">
      <div aria-hidden className="relative z-10 h-4 rounded-full bg-ink shadow-lg" />
      <div className="-mt-2 overflow-hidden px-4">
        <motion.div variants={paperPrinting} className="receipt-tear bg-paper-raised px-6 pt-8 pb-10">
          <p className="text-center font-display font-bold">{facts.lawsuit.shortName}</p>
          <dl className="mt-4 border-t border-dashed border-rule pt-4 font-display figures-tabular">
            {receiptRows.map((row) => (
              <div
                key={row.label}
                className={`flex items-baseline justify-between gap-6 whitespace-nowrap py-1 ${row.emphasis ? "my-2 border-y border-dashed border-rule py-3 font-extrabold" : ""}`}
              >
                <dt>{row.label}</dt>
                <dd>{row.value}</dd>
              </div>
            ))}
          </dl>
        </motion.div>
      </div>
    </div>
  );
}

const LINEAR = [0, 0, 1, 1] as const;

export function DamagesCopy({ waitedSeconds = 0 }: { waitedSeconds?: number }) {
  const countStarts = Math.max(PRINT_DELAY - waitedSeconds, 0);
  const progress = useProgress(PRINT_SECONDS, countStarts, LINEAR);

  return (
    <div className="flex flex-col justify-center">
      <p className="font-display text-display font-extrabold figures-tabular">
        <CountFromProgress progress={progress} total={facts.californiaMinimumDamages.value} format={dollars.format} />
      </p>
      <p className="mt-deck-hairline font-display text-lede font-bold">
        <MaskedLines lines={["minimum damages,", "plus legal fees", "and a year in court"]} delay={0.8} />
      </p>
      <div className="mt-deck-rise">
        <FinePrint delay={1.6}>{`${facts.lawsuit.source}; ${facts.californiaMinimumDamages.source}`}</FinePrint>
      </div>
    </div>
  );
}
