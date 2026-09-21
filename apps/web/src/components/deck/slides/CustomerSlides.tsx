"use client";

import { PersonSimple, PersonSimpleWalk, Wheelchair } from "@phosphor-icons/react";
import { Storefront } from "./SaraSlide";
import { AnimatePresence, motion, type Variants } from "motion/react";
import { useState } from "react";
import type { SlideProps } from "../slides";
import { facts } from "@/lib/facts";
import { easeDrawn, exitTransition } from "@/lib/motion";
import { FinePrint, MaskedLines } from "../primitives";
import { MoonStacks, moonTrips } from "../MoonStacks";

const disability = facts.olderAdultsWithDisability;
const wealth = facts.boomerNetWorthTrillions;

const arrivingCustomers = [
  { Icon: PersonSimpleWalk, delay: 0.9 },
  { Icon: Wheelchair, delay: 1.25 },
  { Icon: PersonSimpleWalk, delay: 1.6 },
];

function customerArrives(delay: number): Variants {
  return {
    enter: { x: "-160%", opacity: 0 },
    present: { x: "0%", opacity: 1, transition: { x: { duration: 1.1, ease: easeDrawn, delay }, opacity: { duration: 0.3, delay } } },
    exit: { opacity: 0, transition: exitTransition },
  };
}

const shopReopens: Variants = {
  enter: { opacity: 0, scaleY: 0.3, scaleX: 1.12 },
  present: { opacity: 1, scaleY: 1, scaleX: 1, transition: { opacity: { duration: 0.2 }, scaleY: { type: "spring", stiffness: 260, damping: 16, delay: 0.1 }, scaleX: { type: "spring", stiffness: 260, damping: 16, delay: 0.1 } } },
  exit: { opacity: 0, transition: exitTransition },
};

function HappyLemonWelcomes() {
  return (
    <div className="relative flex h-deck-art items-end justify-center">
      <motion.div variants={shopReopens} style={{ originY: 1 }} className="flex h-full justify-center">
        <Storefront />
      </motion.div>
      <div role="img" aria-label="Customers, including a wheelchair user, heading into Sara’s shop" className="absolute bottom-0 right-full flex items-end text-headline leading-none">
        {arrivingCustomers.map((customer, index) => (
          <motion.span key={index} variants={customerArrives(customer.delay)} className={index === 1 ? "text-ink" : "text-ink-muted"}>
            <customer.Icon weight="fill" className="icon-em block" />
          </motion.span>
        ))}
      </div>
    </div>
  );
}

export function PivotSlide() {
  return (
    <div className="deck-gutter grid h-full grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)] items-center gap-deck-gap">
      <div>
        <h2 className="font-display text-figure font-extrabold">
          <MaskedLines lines={["Accessibility", "couldn’t be more", "important."]} delay={0.1} />
        </h2>
      </div>
      <HappyLemonWelcomes />
    </div>
  );
}

type BoomerPhase = "disability" | "wealth";
const boomerPhases: BoomerPhase[] = ["disability", "wealth"];

const CROWD = { columns: 10, rows: 5 };
const crowdSize = CROWD.columns * CROWD.rows;

type CrowdColors = { faint: string; ink: string; tape: string };

function readCrowdColors(): CrowdColors {
  const styles = getComputedStyle(document.documentElement);
  const token = (name: string) => styles.getPropertyValue(name).trim();
  return { faint: token("--color-rule"), ink: token("--color-ink"), tape: token("--color-tape-deep") };
}

function hasDisability(index: number) {
  return index % disability.outOf < disability.count;
}

function settledColor(index: number, phase: BoomerPhase, colors: CrowdColors) {
  if (hasDisability(index)) return colors.tape;
  return phase === "disability" ? colors.faint : colors.ink;
}

function CrowdMember({ index, phase, colors }: { index: number; phase: BoomerPhase; colors: CrowdColors }) {
  const arrival = { duration: 0.6, ease: easeDrawn, delay: 0.15 + (index % CROWD.columns) * 0.03 + Math.floor(index / CROWD.columns) * 0.05 };
  return (
    <motion.span
      initial={{ opacity: 0, x: "-60%" }}
      animate={{ opacity: 1, x: "0%", color: settledColor(index, phase, colors) }}
      transition={{ opacity: arrival, x: arrival, color: { duration: 0.5, delay: (index % disability.outOf) * 0.04 } }}
    >
      <PersonSimple weight="fill" className="icon-em block" />
    </motion.span>
  );
}

function BoomerCrowd({ phase }: { phase: BoomerPhase }) {
  const [colors] = useState(readCrowdColors);
  return (
    <motion.div
      layout
      transition={{ layout: { duration: 0.8, ease: easeDrawn } }}
      role="img"
      aria-label={`A crowd of baby boomers where ${disability.count} in every ${disability.outOf} are highlighted`}
      className="grid w-fit grid-cols-10 text-figure leading-none"
    >
      {Array.from({ length: crowdSize }, (_, index) => (
        <CrowdMember key={index} index={index} phase={phase} colors={colors} />
      ))}
    </motion.div>
  );
}

function DisabilityCopy() {
  return (
    <>
      <h2 className="font-display text-headline font-extrabold">
        <MaskedLines lines={[`About ${disability.count} in ${disability.outOf}`, "baby boomers live", "with a disability."]} delay={0.1} />
      </h2>
      <div className="mt-deck-hairline">
        <FinePrint delay={0.9}>{disability.source}</FinePrint>
      </div>
    </>
  );
}

function WealthCopy() {
  return (
    <>
      <h2 className="font-display text-headline font-extrabold figures-tabular">
        <MaskedLines lines={[`They hold $${wealth.value} trillion.`]} delay={0.1} />
      </h2>
      <p className="font-display text-lede font-bold text-ink-muted">
        <MaskedLines lines={[`Stacked in $1 bills, that reaches the Moon ${Math.floor(moonTrips)} times.`]} delay={2.4} />
      </p>
      <div className="mt-deck-hairline">
        <FinePrint delay={3}>{`${wealth.source}; bill thickness ${facts.dollarBillThicknessInches.value} in, ${facts.dollarBillThicknessInches.source}; ${facts.moonDistanceMiles.source}`}</FinePrint>
      </div>
    </>
  );
}

function BoomerCopy({ phase }: { phase: BoomerPhase }) {
  if (phase === "disability") return <DisabilityCopy />;
  return <WealthCopy />;
}

export function BoomersSlide({ step }: SlideProps) {
  const phase = boomerPhases[Math.min(step, boomerPhases.length - 1)];
  const showsMoney = phase === "wealth";

  return (
    <div className="deck-gutter flex h-full flex-col justify-center gap-deck-rise">
      <div className="min-h-deck-copy">
        <AnimatePresence mode="wait" initial={false}>
          <motion.div key={phase} initial="enter" animate="present" exit="exit">
            <BoomerCopy phase={phase} />
          </motion.div>
        </AnimatePresence>
      </div>
      <div className="flex items-end justify-center gap-deck-gap">
        <BoomerCrowd phase={phase} />
        <AnimatePresence>
          {showsMoney && (
            <motion.div
              key="moon"
              layout
              initial="enter"
              animate="present"
              exit="exit"
              className="aspect-[8/9] h-deck-stage"
            >
              <MoonStacks />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
