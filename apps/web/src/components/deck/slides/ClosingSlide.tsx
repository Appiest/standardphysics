"use client";

import { AnimatePresence, animate, motion, useMotionValue, useTransform } from "motion/react";
import { useEffect } from "react";
import { facts } from "@/lib/facts";
import { easeDrawn, easeSweep } from "@/lib/motion";
import { BusinessField } from "../BusinessField";
import { MaskedLines } from "../primitives";
import { ScanWipe } from "../ScanWipe";
import type { SlideProps } from "../slides";

const businesses = facts.smallBusinessesInAmerica.value;
const ZOOM = { startsAfter: 1.6, seconds: 6 };
const WAVE_SECONDS = 2.4;
const wholeNumber = new Intl.NumberFormat("en-US");

function useZoomOut() {
  const businessesLog = useMotionValue(0);
  useEffect(() => {
    const controls = animate(businessesLog, Math.log10(businesses), { duration: ZOOM.seconds, delay: ZOOM.startsAfter, ease: [0.7, 0, 0.35, 1] });
    return () => controls.stop();
  }, [businessesLog]);
  return businessesLog;
}

function useWave(active: boolean) {
  const waveProgress = useMotionValue(0);
  useEffect(() => {
    if (!active) {
      waveProgress.jump(0);
      return;
    }
    const controls = animate(waveProgress, 1, { duration: WAVE_SECONDS, ease: easeSweep });
    return () => controls.stop();
  }, [active, waveProgress]);
  return waveProgress;
}

function SaraBecomesADot() {
  return (
    <motion.div
      className="absolute left-1/2 top-1/2 z-10 -translate-x-1/2 -translate-y-1/2"
      initial={{ scale: 0.6, opacity: 0 }}
      animate={{ scale: [0.6, 1, 1, 0.7], opacity: [0, 1, 1, 0] }}
      transition={{ duration: ZOOM.startsAfter + 0.5, times: [0, 0.25, 0.7, 1], ease: easeDrawn }}
    >
      <motion.img src="/sara.jpg" alt="Sara" className="closing-portrait rounded-full object-cover shadow-2xl" />
    </motion.div>
  );
}

function BusinessCount({ businessesLog }: { businessesLog: ReturnType<typeof useMotionValue<number>> }) {
  const count = useTransform(businessesLog, (value) => wholeNumber.format(Math.min(Math.round(10 ** value), businesses)));
  return (
    <motion.div
      className="relative z-20 flex flex-col items-center bg-paper px-deck-gap py-deck-hairline shadow-xl"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -20 }}
      transition={{ delay: ZOOM.startsAfter + 0.4, duration: 0.6, ease: easeDrawn }}
    >
      <p className="font-display text-headline font-extrabold figures-tabular">
        <motion.span>{count}</motion.span>
      </p>
      <p className="font-display text-lede font-bold">small businesses in America</p>
    </motion.div>
  );
}

function Sendoff() {
  return (
    <motion.div
      key="sendoff"
      className="relative z-20 flex flex-col items-center bg-paper px-deck-gap py-deck-rise text-center shadow-2xl"
      initial="enter"
      animate="present"
      exit="exit"
      variants={{
        enter: { opacity: 0, scale: 0.9 },
        present: { opacity: 1, scale: 1, transition: { delay: WAVE_SECONDS * 0.55, duration: 0.7, ease: easeDrawn } },
        exit: { opacity: 0 },
      }}
    >
      <h1 className="font-display text-headline font-extrabold">
        <ScanWipe delay={WAVE_SECONDS * 0.6} duration={1.2}>
          Standard Physics
        </ScanWipe>
      </h1>
      <p className="mt-deck-hairline font-display text-lede font-bold text-ink-muted">
        <MaskedLines lines={["Making accessibility more accessible."]} delay={WAVE_SECONDS * 0.6 + 1} />
      </p>
    </motion.div>
  );
}

export function ClosingSlide({ step }: SlideProps) {
  const businessesLog = useZoomOut();
  const lightsUp = step > 0;
  const waveProgress = useWave(lightsUp);

  return (
    <div className="relative flex h-full items-end justify-center overflow-hidden pb-deck-rise">
      <BusinessField businessesLog={businessesLog} waveProgress={waveProgress} />
      <SaraBecomesADot />
      <div className="absolute inset-0 flex items-center justify-center">
        <AnimatePresence>{lightsUp && <Sendoff />}</AnimatePresence>
      </div>
      <AnimatePresence>{!lightsUp && <BusinessCount businessesLog={businessesLog} />}</AnimatePresence>
    </div>
  );
}
