"use client";

import { CheckCircle, CircleNotch, Hammer, MagicWand, MinusCircle, Stop } from "@phosphor-icons/react";
import { AnimatePresence, MotionConfig, motion, useReducedMotion } from "motion/react";
import { type RefObject, useEffect, useReducer, useRef, useState } from "react";
import { Explanation } from "@/components/proposal/ProposalReview";
import { Button } from "@/components/ui/Button";
import {
  finishedDetail,
  finishedHeadline,
  fixAllAnnouncement,
  idleDetail,
  isAllCleared,
  namedProblems,
  problemsLeft,
  problemsLeftLabel,
  showMovesLabel,
  stoppedSentence,
  turnClock,
  turnInProgress,
  turnLines,
  turnWork,
} from "@/lib/fix-all-copy";
import { ApiRefusal, modelLoopInfo, streamModelLoop } from "@/lib/layout-client";
import { advanceModelLoop, type ModelLoopProgress, NOT_STARTED } from "@/lib/model-loop-progress";
import { easeDrawn, easeSweep } from "@/lib/motion";
import type { ModelLoopEvent, NodeMove } from "@/types/contracts";

type Props = {
  scanId: string;
  revision: number;
  /** Puts the finished layout on the plan, with the pieces the run proposes moving so they can be picked out. */
  onOpen: (moves: NodeMove[], proposed: string[]) => void;
  /** The owner's unsaved moves; given, the loop fixes that layout instead of the saved shop. */
  plan?: NodeMove[];
};
type CardProps = Props & { label: string; onOneAtATime?: () => void };

const UNABLE_TO_START = "Unable to start. Check your connection, then try again.";
const SNAP = { type: "spring", duration: 0.3, bounce: 0 } as const;
const GROW = { type: "spring", duration: 0.5, bounce: 0 } as const;
const BLURRED_IN = { opacity: 0, y: 8, filter: "blur(4px)" };
const SHARP = { opacity: 1, y: 0, filter: "blur(0px)" };
const ICON_IN = { opacity: 0, scale: 0.25, filter: "blur(4px)" };
const ICON_SHOWN = { opacity: 1, scale: 1, filter: "blur(0px)" };

/** The model's label when one is set up to run the loop; null while asking or when none is. */
export function useModelLabel(): string | null {
  const [label, setLabel] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    modelLoopInfo().then((info) => { if (live && info.available) setLabel(info.label || "The layout model"); }).catch(() => undefined);
    return () => { live = false; };
  }, []);
  return label;
}

/** The element's rendered height, kept current as its content changes, so the card can animate real height. */
function useContentHeight(): [RefObject<HTMLDivElement | null>, number | null] {
  const ref = useRef<HTMLDivElement | null>(null);
  const [height, setHeight] = useState<number | null>(null);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setHeight(entry.borderBoxSize[0].blockSize));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, height];
}

function useModelLoop(scanId: string, revision: number, plan: NodeMove[] | undefined) {
  const [progress, dispatch] = useReducer(advanceModelLoop, NOT_STARTED);
  const connection = useRef<AbortController | null>(null);
  const latestPlan = useRef(plan);
  useEffect(() => { latestPlan.current = plan; }, [plan]);
  useEffect(() => () => connection.current?.abort(), []);

  async function start() {
    const controller = new AbortController();
    connection.current = controller;
    dispatch({ kind: "start" });
    try {
      await streamModelLoop(scanId, revision, dispatch, controller.signal, latestPlan.current ?? []);
      dispatch({ kind: "closed" });
    } catch (reason) {
      if (controller.signal.aborted) return;
      dispatch({ kind: "refused", error: reason instanceof ApiRefusal && reason.error ? reason.error : UNABLE_TO_START });
    }
  }

  function stop() {
    dispatch({ kind: "stop" });
    connection.current?.abort();
  }

  return { progress, start, stop };
}

type IdleProps = { label: string; fromPlan: boolean; onStart: () => void; onOneAtATime?: () => void };

function Idle({ label, fromPlan, onStart, onOneAtATime }: IdleProps) {
  return (
    <motion.div className="flex flex-col items-start gap-3" exit={{ opacity: 0, transition: { duration: 0.15 } }}>
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="primary" onClick={onStart}>
          <MagicWand size={18} weight="bold" aria-hidden />
          {fromPlan ? "Fix this layout" : "Fix room"}
        </Button>
        {onOneAtATime && <Button variant="quiet" onClick={onOneAtATime}>Fix one at a time</Button>}
      </div>
      <p className="text-pretty text-sm text-ink-muted">{idleDetail(label, fromPlan)}</p>
    </motion.div>
  );
}

function RollingCount({ value }: { value: number | null }) {
  if (value === null) return <CircleNotch size={40} className="text-ink-faint motion-safe:animate-spin" aria-hidden />;
  return (
    <span className={`relative inline-flex overflow-hidden font-display text-5xl font-semibold leading-none tabular-nums transition-colors duration-300 ${value === 0 ? "text-pass" : "text-ink"}`}>
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.span key={value} initial={{ y: "-100%", opacity: 0, filter: "blur(4px)" }}
          animate={{ y: 0, opacity: 1, filter: "blur(0px)" }} exit={{ y: "100%", opacity: 0, filter: "blur(4px)" }}
          transition={{ type: "spring", duration: 0.45, bounce: 0 }}>
          {value}
        </motion.span>
      </AnimatePresence>
    </span>
  );
}

const FIRST_VOLLEY = Array.from({ length: 12 }, (_, index) => index * 30);
const SECOND_VOLLEY = FIRST_VOLLEY.map((degrees) => degrees + 15);

function Volley({ angles, reach, delay }: { angles: number[]; reach: number; delay: number }) {
  return angles.map((degrees, index) => (
    <span key={degrees} className="absolute left-0 top-0" style={{ transform: `rotate(${degrees}deg)` }}>
      <motion.span className={`absolute -top-px left-0 block h-0.5 w-3 origin-left rounded-full ${index % 2 ? "bg-accent" : "bg-pass"}`}
        initial={{ x: 6, scaleX: 0.3, opacity: 0 }}
        animate={{ x: [6, reach], scaleX: [0.3, 1, 0.2], opacity: [0, 1, 0] }}
        transition={{ duration: 0.8, ease: easeDrawn, delay: delay + (index % 3) * 0.04 }} />
    </span>
  ));
}

/** Two small volleys of sparks and a ring from the count when the last problem clears. */
function Burst() {
  if (useReducedMotion()) return null;
  return (
    <span aria-hidden className="pointer-events-none absolute left-1/2 top-1/2">
      <motion.span className="absolute -left-6 -top-6 size-12 rounded-full ring-2 ring-pass"
        initial={{ scale: 0.4, opacity: 0.9 }} animate={{ scale: 2.4, opacity: 0 }}
        transition={{ duration: 0.7, ease: easeDrawn }} />
      <Volley angles={FIRST_VOLLEY} reach={52} delay={0} />
      <Volley angles={SECOND_VOLLEY} reach={34} delay={0.22} />
    </span>
  );
}

function Track({ started, left, working }: { started: number; left: number; working: boolean }) {
  return (
    <div className="relative mt-4 h-1.5 overflow-hidden rounded-full bg-ink/[0.08]">
      <motion.div className="absolute inset-0 origin-left rounded-full bg-pass" initial={false}
        animate={{ scaleX: (started - left) / started }} transition={GROW} />
      {working && (
        <motion.div aria-hidden className="absolute inset-y-0 w-1/3 bg-linear-to-r from-transparent via-accent/45 to-transparent"
          initial={{ x: "-100%" }} animate={{ x: "300%" }}
          transition={{ duration: 1.6, ease: easeSweep, repeat: Infinity }} />
      )}
    </div>
  );
}

function Tally({ progress }: { progress: ModelLoopProgress }) {
  const left = problemsLeft(progress);
  const started = progress.startedWith;
  const celebrate = progress.phase === "finished" && isAllCleared(progress.finished, started);
  return (
    <div>
      <div className="flex items-end gap-3">
        <span className="relative inline-flex">
          <RollingCount value={left} />
          {celebrate && <Burst />}
        </span>
        <p className="pb-1 text-ink-muted">{left === null ? "Counting what can be fixed" : problemsLeftLabel(left)}</p>
      </div>
      {started !== null && started > 0 && left !== null && <Track started={started} left={left} working={progress.phase === "running"} />}
    </div>
  );
}

function TurnMark({ moved }: { moved: boolean }) {
  const Icon = moved ? CheckCircle : MinusCircle;
  return (
    <motion.span className="mt-0.5 shrink-0" initial={ICON_IN} animate={ICON_SHOWN} transition={{ ...SNAP, delay: 0.12 }}>
      <Icon size={20} weight={moved ? "fill" : "regular"} className={moved ? "text-pass" : "text-ink-faint"} aria-hidden />
    </motion.span>
  );
}

function TurnRow({ turn }: { turn: ModelLoopEvent }) {
  return (
    <motion.li className="flex gap-3" initial={BLURRED_IN} animate={SHARP} transition={GROW}>
      <TurnMark moved={turn.picked.length > 0} />
      <div className="min-w-0">
        <ul className="flex flex-col gap-1">
          {turnLines(turn).map((line) => (
            <li key={line.text}>
              <span className="font-semibold">{line.text}</span>
              {line.construction && (
                <span className="mt-0.5 flex items-center gap-1.5 text-sm text-attention">
                  <Hammer size={14} weight="bold" aria-hidden />
                  Needs a contractor
                </span>
              )}
            </li>
          ))}
        </ul>
        {turn.why && <p className="mt-0.5 text-pretty text-sm text-ink-muted">{turn.why}</p>}
      </div>
    </motion.li>
  );
}

/** Whole seconds since the component mounted, ticking once a second. */
function useSecondsRunning(): number {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const began = Date.now();
    const timer = setInterval(() => setSeconds(Math.floor((Date.now() - began) / 1000)), 1000);
    return () => clearInterval(timer);
  }, []);
  return seconds;
}

/** The turn under way: its number and clock, the problems it is working on, and what it is doing meanwhile. */
function WorkingRow({ label, progress }: { label: string; progress: ModelLoopProgress }) {
  const seconds = useSecondsRunning();
  const { named, more } = namedProblems(progress.workingOn);
  return (
    <motion.li className="flex gap-3" initial={BLURRED_IN} animate={SHARP}
      exit={{ opacity: 0, y: -4, transition: { duration: 0.15 } }} transition={GROW}>
      <CircleNotch size={20} className="mt-0.5 shrink-0 text-accent motion-safe:animate-spin" aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-3">
          <p className="font-semibold">{turnInProgress(progress.turns.length + 1, progress.turnsAtMost)}</p>
          <span className="text-sm tabular-nums text-ink-muted" aria-hidden>{turnClock(seconds)}</span>
        </div>
        {named.length > 0 && (
          <ul className="mt-1.5 flex flex-col gap-1 text-sm" aria-label="Problems this turn is working on">
            {named.map((title) => (
              <li key={title} className="flex items-start gap-2">
                <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-accent motion-safe:animate-pulse" aria-hidden />
                <span className="text-pretty">{title}</span>
              </li>
            ))}
            {more > 0 && <li className="pl-3.5 text-ink-muted">and {more} more</li>}
          </ul>
        )}
        <p className="mt-1.5 text-pretty text-sm text-ink-muted">{turnWork(label)}</p>
      </div>
    </motion.li>
  );
}

function Turns({ progress, label }: { progress: ModelLoopProgress; label: string }) {
  if (progress.turns.length === 0 && progress.phase !== "running") return null;
  return (
    <ol className="mt-5 flex flex-col gap-4" aria-label="Moves the model made">
      <AnimatePresence initial={false}>
        {progress.turns.map((turn) => <TurnRow key={turn.turn ?? turn.picked.join()} turn={turn} />)}
        {progress.phase === "running" && <WorkingRow key={`working-${progress.turns.length}`} label={label} progress={progress} />}
      </AnimatePresence>
    </ol>
  );
}

/** Moves focus to the element once it mounts, without scrolling the count and its burst out of view. */
function useQuietFocus() {
  const ref = useRef<HTMLButtonElement | null>(null);
  useEffect(() => ref.current?.focus({ preventScroll: true }), []);
  return ref;
}

function Finished({ finished, started, onOpen }: { finished: ModelLoopEvent; started: number; onOpen: Props["onOpen"] }) {
  const allCleared = isAllCleared(finished, started);
  const proposes = finished.proposed.length > 0;
  const openButton = useQuietFocus();
  return (
    <motion.div className="mt-5 flex flex-col items-start gap-3" initial={BLURRED_IN} animate={SHARP} transition={{ ...GROW, delay: allCleared ? 0.35 : 0 }}>
      <div>
        <p className="text-lg font-semibold">{finishedHeadline(finished, started)}</p>
        <p className="mt-1 text-pretty text-ink-muted">{finishedDetail(finished, allCleared)}</p>
      </div>
      {proposes && finished.explanation && <Explanation explanation={finished.explanation} />}
      {proposes && (
        <Button ref={openButton} variant="primary" onClick={() => onOpen(finished.moves, finished.proposed)}>{showMovesLabel(finished.proposed.length)}</Button>
      )}
    </motion.div>
  );
}

type EndingProps = { progress: ModelLoopProgress; onOpen: Props["onOpen"]; onStart: () => void; onStop: () => void };

function Ending({ progress, onOpen, onStart, onStop }: EndingProps) {
  if (progress.phase === "running") {
    return (
      <Button variant="quiet" className="mt-4 -ml-3" autoFocus onClick={onStop}>
        <Stop size={16} weight="bold" aria-hidden />
        Stop fixing
      </Button>
    );
  }
  if (progress.finished) return <Finished finished={progress.finished} started={progress.startedWith ?? 0} onOpen={onOpen} />;
  const stopped = progress.phase === "stopped";
  return (
    <div className="mt-5 flex flex-col items-start gap-3">
      <p className={stopped ? "text-ink-muted" : "text-problem"}>{stopped ? stoppedSentence(progress.turns.length) : progress.error}</p>
      <Button variant="choice" onClick={onStart}>Try again</Button>
    </div>
  );
}

function Run({ progress, label, onOpen, onStart, onStop }: EndingProps & { label: string }) {
  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.2, delay: 0.1 }}>
      <Tally progress={progress} />
      <Turns progress={progress} label={label} />
      <Ending progress={progress} onOpen={onOpen} onStart={onStart} onStop={onStop} />
    </motion.div>
  );
}

/** One press asks the layout model to work through every open problem, turn by turn, and shows each move land. */
export function FixAll({ scanId, revision, onOpen, label, onOneAtATime, plan }: CardProps) {
  const { progress, start, stop } = useModelLoop(scanId, revision, plan);
  const fromPlan = plan !== undefined;
  const [content, height] = useContentHeight();
  const [growing, setGrowing] = useState(false);
  return (
    <MotionConfig reducedMotion="user">
      <p className="sr-only" role="status">{fixAllAnnouncement(progress)}</p>
      <motion.article initial={false} animate={{ height: height ?? "auto" }} transition={GROW}
        onAnimationStart={() => setGrowing(true)} onAnimationComplete={() => setGrowing(false)}
        aria-label={fromPlan ? "Fix this layout" : "Fix room"} className={`rounded-2xl bg-sheet shadow-float ${growing ? "[clip-path:inset(-4rem_-4rem_0_-4rem_round_0_0_1rem_1rem)]" : ""}`}>
        <div ref={content} className="p-4">
          <AnimatePresence mode="wait" initial={false}>
            {progress.phase === "idle"
              ? <Idle key="idle" label={label} fromPlan={fromPlan} onStart={start} onOneAtATime={onOneAtATime} />
              : <Run key="run" progress={progress} label={label} onOpen={onOpen} onStart={start} onStop={stop} />}
          </AnimatePresence>
        </div>
      </motion.article>
    </MotionConfig>
  );
}
