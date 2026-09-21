"use client";

import { animate, motion, useMotionValue } from "motion/react";
import { useEffect } from "react";
import { facts } from "@/lib/facts";
import { CountFromProgress, FinePrint, MaskedLines, useProgress } from "./primitives";

const wholeNumber = new Intl.NumberFormat("en-US");
const paidPercent = facts.casesWithMoneyPercent.value;
const unpaidShare = 1 - paidPercent / 100;
const BLEED = { starts: 1.2, bills: 46 };

function noise(seed: number) {
  const value = Math.sin(seed * 12.9898) * 43758.5453;
  return value - Math.floor(value);
}

const bills = Array.from({ length: BLEED.bills }, (_, index) => ({
  key: index,
  left: 4 + noise(index + 0.7) * 92,
  top: 2 + noise(index + 0.3) * 90,
  drift: (noise(index + 0.2) - 0.5) * 8,
  spin: (noise(index + 0.9) - 0.5) * 90,
  seconds: 1.8 + noise(index + 0.4) * 1.4,
  delay: BLEED.starts + noise(index + 0.6) * 2.4,
  pause: noise(index + 0.8) * 1.2,
}));

function FallingBill({ bill }: { bill: (typeof bills)[number] }) {
  return (
    <motion.span
      aria-hidden
      className="payout-bill absolute flex items-center justify-center bg-paper-raised"
      style={{ left: `${bill.left}%`, top: `${bill.top}%` }}
      initial={{ opacity: 0 }}
      animate={{ x: ["0vh", `${bill.drift * 0.4}vh`, `${bill.drift}vh`], y: ["0vh", "8vh", "95vh"], rotate: [0, bill.spin, -bill.spin], opacity: [0, 1, 1] }}
      transition={{ duration: bill.seconds, delay: bill.delay, repeat: Infinity, repeatDelay: bill.pause, ease: "easeIn", times: [0, 0.25, 1] }}
    >
      <span className="payout-bill-seal rounded-full" />
    </motion.span>
  );
}

export function useUnpaidFade(active: boolean) {
  const fade = useMotionValue(0);
  useEffect(() => {
    if (!active) {
      fade.jump(0);
      return;
    }
    const controls = animate(fade, 1, { duration: 0.9, delay: 0.5 });
    return () => controls.stop();
  }, [active, fade]);
  return fade;
}

export const payoutUnpaidShare = unpaidShare;

export function DrainingBills() {
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0">
      {bills.map((bill) => (
        <FallingBill key={bill.key} bill={bill} />
      ))}
    </div>
  );
}

export function PayoutsCopy() {
  const progress = useProgress(1.1, 0.3);
  return (
    <div className="flex flex-col gap-deck-hairline">
      <p className="font-display text-display font-extrabold figures-tabular">
        <CountFromProgress progress={progress} total={paidPercent} format={(value) => `${value}%`} />
      </p>
      <p className="font-display text-lede font-bold">
        <MaskedLines lines={["of ADA cases reported in", "California ended in a payout"]} delay={0.5} />
      </p>
      <div className="mt-deck-rise">
        <FinePrint delay={1.6}>{`${facts.casesWithMoneyPercent.source}, ${wholeNumber.format(facts.casesWithMoneyPercent.caseReports)} California case reports`}</FinePrint>
      </div>
    </div>
  );
}
