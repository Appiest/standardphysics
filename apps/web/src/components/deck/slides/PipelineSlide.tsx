"use client";

import { Cube, DeviceMobile, ListChecks, MapPin, Tag, type Icon } from "@phosphor-icons/react";
import { AnimatePresence, motion, type Variants } from "motion/react";
import { AppCapture } from "../AppCapture";
import type { SlideProps } from "../slides";
import { easeDrawn, exitTransition } from "@/lib/motion";

type PipelineStep = { Icon: Icon; title: string; detail: string; tool?: string };

const steps: PipelineStep[] = [
  { Icon: DeviceMobile, title: "LiDAR scan", detail: "Apple RoomPlan on Sara’s iPhone" },
  { Icon: Cube, title: "Measured 3D model", detail: "Every wall, door and fixture" },
  { Icon: Tag, title: "Label each object", detail: "Astra through", tool: "OpenRouter" },
  { Icon: ListChecks, title: "Check the ADA Standards", detail: "Cited rules, traced in", tool: "W&B Weave" },
  { Icon: MapPin, title: "Pin each problem", detail: "The exact spot, rendered in Blender" },
];

const STEP_GAP = 0.45;
const FIRST_STEP = 0.3;

function stepAppears(index: number): Variants {
  return {
    enter: { opacity: 0, x: -24 },
    present: { opacity: 1, x: 0, transition: { duration: 0.6, ease: easeDrawn, delay: FIRST_STEP + index * STEP_GAP } },
    exit: { opacity: 0, transition: exitTransition },
  };
}

function connectorGrows(index: number): Variants {
  return {
    enter: { scaleY: 0 },
    present: { scaleY: 1, transition: { duration: STEP_GAP, ease: "linear", delay: FIRST_STEP + index * STEP_GAP + 0.2 } },
  };
}

function StepRow({ step, index }: { step: PipelineStep; index: number }) {
  const isLast = index === steps.length - 1;
  return (
    <motion.li variants={stepAppears(index)} className="relative flex items-center gap-deck-hairline">
      {!isLast && <motion.span aria-hidden variants={connectorGrows(index)} className="step-connector absolute h-full origin-top bg-ink" />}
      <span className={`step-badge relative flex items-center justify-center rounded-full ${isLast ? "bg-tape" : "bg-ink text-paper"}`}>
        <step.Icon weight="bold" className="step-icon" />
      </span>
      <span className="flex flex-col">
        <span className="font-display font-extrabold">{step.title}</span>
        <span className="font-display text-caption font-bold text-ink-muted">
          {step.detail}
          {step.tool && <span className="text-ink"> {step.tool}</span>}
        </span>
      </span>
    </motion.li>
  );
}

export function PipelineSlide({ step }: SlideProps) {
  return (
    <div className="deck-gutter grid h-full grid-cols-[auto_minmax(0,1fr)] items-center gap-deck-gap">
      <AnimatePresence>
        {step > 0 && (
          <motion.div key="report" className="col-start-2 row-start-1" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <AppCapture aspect="square" image="/deck/app-report.jpg" alt="The Standard Physics report pinning the 47 inch counter and the 31 inch aisle, each with a render of the exact spot and the fix" />
          </motion.div>
        )}
      </AnimatePresence>
      <ol aria-label="How Standard Physics checks a scan" className="col-start-1 row-start-1 flex flex-col gap-deck-rise text-lede">
        {steps.map((step, index) => (
          <StepRow key={step.title} step={step} index={index} />
        ))}
      </ol>
    </div>
  );
}
