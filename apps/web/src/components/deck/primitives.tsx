"use client";

import { animate, motion, useMotionValue, useTransform, type MotionValue, type Variants } from "motion/react";
import { useEffect, type ReactNode } from "react";
import { easeDrawn, exitTransition } from "@/lib/motion";

export function lineReveal(delay: number): Variants {
  return {
    enter: { y: "108%" },
    present: { y: "0%", transition: { duration: 1, ease: easeDrawn, delay } },
    exit: { y: "-108%", transition: exitTransition },
  };
}

export function fadeReveal(delay: number, rise = 0): Variants {
  return {
    enter: { opacity: 0, y: rise },
    present: { opacity: 1, y: 0, transition: { duration: 0.9, ease: easeDrawn, delay } },
    exit: { opacity: 0, transition: exitTransition },
  };
}

type MaskedLinesProps = {
  lines: string[];
  delay?: number;
  stagger?: number;
};

export function MaskedLines({ lines, delay = 0, stagger = 0.09 }: MaskedLinesProps) {
  return lines.map((line, index) => (
    <span key={line} className="line-mask">
      <motion.span className="block" variants={lineReveal(delay + index * stagger)}>
        {line}
      </motion.span>
    </span>
  ));
}

export function FinePrint({ children, delay = 1.2 }: { children: ReactNode; delay?: number }) {
  return (
    <motion.p variants={fadeReveal(delay)} className="font-display text-fineprint text-ink-muted">
      {children}
    </motion.p>
  );
}

export function useProgress(duration: number, delay: number, ease: readonly number[] = easeDrawn) {
  const progress = useMotionValue(0);

  useEffect(() => {
    const controls = animate(progress, 1, { duration, delay, ease: [...ease] as [number, number, number, number] });
    return () => controls.stop();
  }, [progress, duration, delay, ease]);

  return progress;
}

const wholeNumber = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

export function CountFromProgress({ progress, total, format = (value) => wholeNumber.format(value) }: {
  progress: MotionValue<number>;
  total: number;
  format?: (value: number) => string;
}) {
  const text = useTransform(progress, (value) => format(Math.round(value * total)));
  const opacity = useTransform(progress, (value) => (value > 0 ? 1 : 0));
  return (
    <span className="inline-grid">
      <span aria-hidden className="invisible col-start-1 row-start-1">{format(total)}</span>
      <motion.span className="col-start-1 row-start-1" style={{ opacity }}>
        {text}
      </motion.span>
    </span>
  );
}
