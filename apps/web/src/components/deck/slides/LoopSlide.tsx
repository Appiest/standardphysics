"use client";

import { ArrowsClockwise, ListChecks, Scales, Wrench, type Icon } from "@phosphor-icons/react";
import { AnimatePresence, animate, motion, useMotionValue, useTransform, type Variants } from "motion/react";
import { useEffect } from "react";
import { easeDrawn, exitTransition } from "@/lib/motion";
import { AppCapture } from "../AppCapture";
import { MaskedLines } from "../primitives";
import type { SlideProps } from "../slides";

type LoopPhase = "loop" | "decide" | "test" | "improve";
const loopPhases: LoopPhase[] = ["loop", "decide", "test", "improve"];

const RING = { cx: 500, cy: 470, r: 300 };
const NODE_RADIUS = 92;
const LAP_SECONDS = 6;

type LoopNode = { key: string; Icon: Icon; title: string; tool: string; degrees: number };

const nodes: LoopNode[] = [
  { key: "measure", Icon: ListChecks, title: "Check", tool: "ADA rules", degrees: -90 },
  { key: "decide", Icon: Scales, title: "Decide", tool: "TypeSafe", degrees: 0 },
  { key: "act", Icon: Wrench, title: "Act", tool: "Fix agent", degrees: 90 },
  { key: "recheck", Icon: ArrowsClockwise, title: "Re-check", tool: "W&B Weave", degrees: 180 },
];

const routerActions = ["FIX", "RESCAN_AREA", "ASK_OWNER", "ESCALATE", "DONE"];

function pointOnRing(degrees: number, radius = RING.r) {
  const radians = (degrees * Math.PI) / 180;
  return { x: RING.cx + Math.cos(radians) * radius, y: RING.cy + Math.sin(radians) * radius };
}

function arcBetween(fromDegrees: number, toDegrees: number) {
  const gap = (NODE_RADIUS / RING.r) * (180 / Math.PI) + 4;
  const start = pointOnRing(fromDegrees + gap);
  const end = pointOnRing(toDegrees - gap);
  return `M ${start.x} ${start.y} A ${RING.r} ${RING.r} 0 0 1 ${end.x} ${end.y}`;
}

function nodeAppears(index: number): Variants {
  return {
    enter: { scale: 0.6, opacity: 0 },
    present: { scale: 1, opacity: 1, transition: { duration: 0.5, ease: easeDrawn, delay: 0.2 + index * 0.25 } },
    exit: { opacity: 0, transition: exitTransition },
  };
}

function arcDraws(index: number): Variants {
  return {
    enter: { pathLength: 0 },
    present: { pathLength: 1, transition: { duration: 0.45, ease: "easeInOut", delay: 0.45 + index * 0.25 } },
    exit: { opacity: 0, transition: exitTransition },
  };
}

function useLapToken() {
  const lap = useMotionValue(0);
  useEffect(() => {
    const controls = animate(lap, 1, { duration: LAP_SECONDS, delay: 1.6, ease: "linear", repeat: Infinity });
    return () => controls.stop();
  }, [lap]);
  const x = useTransform(lap, (value) => pointOnRing(-90 + value * 360).x);
  const y = useTransform(lap, (value) => pointOnRing(-90 + value * 360).y);
  return { x, y };
}

function LapToken() {
  const { x, y } = useLapToken();
  return (
    <motion.circle
      r={16}
      cx={x}
      cy={y}
      fill="var(--color-tape)"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1, transition: { delay: 1.6, duration: 0.3 } }}
    />
  );
}

function RingNode({ node, index, highlighted }: { node: LoopNode; index: number; highlighted: boolean }) {
  const center = pointOnRing(node.degrees);
  return (
    <motion.g variants={nodeAppears(index)} style={{ originX: `${center.x}px`, originY: `${center.y}px` }}>
      <motion.circle
        cx={center.x}
        cy={center.y}
        r={NODE_RADIUS}
        initial={false}
        animate={{ fill: highlighted ? "var(--color-ink)" : "var(--color-paper-raised)" }}
        stroke="var(--color-ink)"
        strokeWidth={6}
      />
      <foreignObject x={center.x - NODE_RADIUS} y={center.y - NODE_RADIUS} width={NODE_RADIUS * 2} height={NODE_RADIUS * 2}>
        <div className={`flex size-full flex-col items-center justify-center font-display leading-none ${highlighted ? "text-paper" : "text-ink"}`}>
          <node.Icon weight="bold" className="loop-node-icon" />
          <span className="loop-node-title font-extrabold">{node.title}</span>
          <span className={`loop-node-tool font-bold ${highlighted ? "text-tape" : "text-ink-muted"}`}>{node.tool}</span>
        </div>
      </foreignObject>
    </motion.g>
  );
}

function ActionChoices({ visible }: { visible: boolean }) {
  const decide = pointOnRing(0);
  return (
    <AnimatePresence>
      {visible && (
        <motion.g key="choices" initial="enter" animate="present" exit="exit">
          {routerActions.map((action, index) => {
            const y = decide.y - 160 + index * 80;
            const chosen = action === "FIX";
            return (
              <motion.g
                key={action}
                variants={{
                  enter: { opacity: 0, x: -30 },
                  present: { opacity: 1, x: 0, transition: { duration: 0.4, ease: easeDrawn, delay: 0.1 + index * 0.08 } },
                  exit: { opacity: 0, transition: exitTransition },
                }}
              >
                <line x1={decide.x + NODE_RADIUS} y1={decide.y} x2={decide.x + 150} y2={y} stroke={chosen ? "var(--color-tape-deep)" : "var(--color-rule)"} strokeWidth={chosen ? 6 : 3} />
                <rect x={decide.x + 150} y={y - 28} width={290} height={56} rx={8} fill={chosen ? "var(--color-tape)" : "var(--color-paper-raised)"} />
                <text x={decide.x + 295} y={y + 10} textAnchor="middle" fill={chosen ? "var(--color-ink)" : "var(--color-ink-muted)"} className="font-display text-3xl font-extrabold">
                  {action}
                </text>
              </motion.g>
            );
          })}
        </motion.g>
      )}
    </AnimatePresence>
  );
}

function LoopDiagram({ phase }: { phase: LoopPhase }) {
  return (
    <svg viewBox="90 60 1180 820" className="h-full w-full overflow-visible" role="img" aria-label="A loop: check the shop, TypeSafe decides the next action, the fix agent acts, and the change is re-checked and traced in W&B Weave">
      {nodes.map((node, index) => (
        <motion.path
          key={node.key}
          d={arcBetween(node.degrees, nodes[(index + 1) % nodes.length].degrees + (index === nodes.length - 1 ? 360 : 0))}
          fill="none"
          stroke="var(--color-ink)"
          strokeWidth={6}
          strokeLinecap="round"
          variants={arcDraws(index)}
        />
      ))}
      <LapToken />
      {nodes.map((node, index) => (
        <RingNode key={node.key} node={node} index={index} highlighted={(phase === "decide" && node.key === "decide") || (phase === "improve" && node.key === "recheck")} />
      ))}
      <ActionChoices visible={phase === "decide"} />
    </svg>
  );
}

const copy: Record<LoopPhase, { headline: string[]; detail: string[] }> = {
  loop: {
    headline: ["Standard Physics", "is a controlled,", "observable", "agentic loop."],
    detail: ["It checks the shop, decides, acts,", "and checks again until the problems clear."],
  },
  decide: {
    headline: ["TypeSafe picks", "the next move."],
    detail: [],
  },
  test: {
    headline: ["Every fix is", "tested before", "Sara sees it."],
    detail: ["The agent moves furniture", "in the model, re-checks the", "shop, and keeps a change only", "if nothing new breaks."],
  },
  improve: {
    headline: ["Weave traces", "every pass and", "scores the loop."],
    detail: [],
  },
};

function LoopCopy({ phase }: { phase: LoopPhase }) {
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
    </>
  );
}

export function LoopSlide({ step }: SlideProps) {
  const phase = loopPhases[Math.min(step, loopPhases.length - 1)];
  return (
    <div className={`deck-gutter grid h-full items-center gap-deck-gap transition-[grid-template-columns] duration-700 ${phase === "test" ? "grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)]" : "grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]"}`}>
      <div>
        <AnimatePresence mode="wait" initial={false}>
          <motion.div key={phase} initial="enter" animate="present" exit="exit">
            <LoopCopy phase={phase} />
          </motion.div>
        </AnimatePresence>
      </div>
      <div className="flex h-deck-art items-center">
        <AnimatePresence mode="wait" initial={false}>
          {phase === "test" ? (
            <AppCapture key="fix-loop" video="/deck/fix-loop.mp4" alt="Standard Physics running Fix what I can: it moves a display case, re-measures, keeps the change, and tries two more moves that it rejects" />
          ) : (
            <motion.div key="diagram" className="size-full" initial="enter" animate="present" exit="exit">
              <LoopDiagram phase={phase} />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
