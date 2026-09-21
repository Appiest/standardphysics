"use client";

import { AnimatePresence, animate, motion, useMotionValue, useTransform, type MotionValue, type Transition, type Variants } from "motion/react";
import { facts } from "@/lib/facts";
import { useCallback, useEffect, useState } from "react";
import { easeDrawn, exitTransition } from "@/lib/motion";
import { CountFromProgress, FinePrint, MaskedLines } from "../primitives";
import { DotField, type FieldOrigin } from "../DotField";
import { DrainingBills, PayoutsCopy, payoutUnpaidShare, useUnpaidFade } from "../Payouts";
import { lawsuitFieldCount, sarasShopInField } from "../lawsuitField";
import type { SlideProps } from "../slides";
import { BobaCup } from "../BobaCup";
import { DamagesCopy, Receipt } from "../Receipt";

const stroke = { stroke: "var(--color-ink)", strokeWidth: 7, strokeLinecap: "round", strokeLinejoin: "round" } as const;

const FACADE = { left: 50, right: 470, top: 150, bottom: 580 };
const AWNING = { top: 150, bottom: 215, scallops: 7 };
const WINDOW = { x: 90, y: 270, width: 190, height: 200 };
const DOOR = { x: 320, y: 270, width: 110, height: 310 };

const scallopWidth = (FACADE.right - FACADE.left) / AWNING.scallops;
const scallopedEdge = Array.from({ length: AWNING.scallops }, (_, index) => {
  const endX = FACADE.right - (index + 1) * scallopWidth;
  return `Q ${endX + scallopWidth / 2} ${AWNING.bottom + 34} ${endX} ${AWNING.bottom}`;
}).join(" ");
const awningPath = `M ${FACADE.left - 20} ${AWNING.top} L ${FACADE.right + 20} ${AWNING.top} L ${FACADE.right} ${AWNING.bottom} ${scallopedEdge} Z`;
const awningStripes = Array.from({ length: AWNING.scallops }, (_, index) => FACADE.left + index * scallopWidth).filter((_, index) => index % 2 === 0);

function drawn(delay: number, duration = 0.8): Variants {
  return {
    enter: { pathLength: 0, opacity: 0 },
    present: { pathLength: 1, opacity: 1, transition: { pathLength: { duration, ease: easeDrawn, delay }, opacity: { duration: 0.01, delay } } },
    exit: { opacity: 0, transition: exitTransition },
  };
}

function dropsIn(delay: number, distance: number): Variants {
  return {
    enter: { y: -distance, opacity: 0 },
    present: { y: 0, opacity: 1, transition: { y: { type: "spring", stiffness: 320, damping: 20, delay }, opacity: { duration: 0.15, delay } } },
    exit: { opacity: 0, transition: exitTransition },
  };
}

const saraStepsOut: Variants = {
  enter: { y: 40, opacity: 0 },
  present: { y: 0, opacity: 1, transition: { duration: 0.6, ease: easeDrawn, delay: 1.25 } },
  exit: { opacity: 0, transition: exitTransition },
};

function Awning() {
  return (
    <motion.g variants={dropsIn(0.75, 90)}>
      <clipPath id="awning-shape">
        <path d={awningPath} />
      </clipPath>
      <g clipPath="url(#awning-shape)">
        <path d={awningPath} fill="var(--color-paper-raised)" />
        {awningStripes.map((x) => (
          <rect key={x} x={x} y={AWNING.top} width={scallopWidth} height={AWNING.bottom - AWNING.top + 40} fill="var(--color-tape)" />
        ))}
      </g>
      <path d={awningPath} fill="none" {...stroke} />
    </motion.g>
  );
}

function WindowCup() {
  const size = { width: WINDOW.width * 0.62, height: WINDOW.height * 0.86 };
  return (
    <foreignObject x={WINDOW.x + (WINDOW.width - size.width) / 2} y={WINDOW.y + (WINDOW.height - size.height) / 2 + 6} width={size.width} height={size.height}>
      <BobaCup className="size-full" />
    </foreignObject>
  );
}

function Sara() {
  const centerX = DOOR.x + DOOR.width / 2;
  const feet = DOOR.y + DOOR.height - 8;
  return (
    <motion.g variants={saraStepsOut}>
      <circle cx={centerX} cy={feet - 185} r={26} fill="var(--color-ink)" />
      <path d={`M ${centerX - 38} ${feet} L ${centerX - 34} ${feet - 110} Q ${centerX - 32} ${feet - 150} ${centerX} ${feet - 150} Q ${centerX + 32} ${feet - 150} ${centerX + 34} ${feet - 110} L ${centerX + 38} ${feet} Z`} fill="var(--color-ink)" />
    </motion.g>
  );
}

export function Storefront() {
  return (
    <svg viewBox="0 0 520 620" className="h-full max-h-full w-auto overflow-visible" role="img" aria-label="Sara standing in the doorway of her boba shop">
      <motion.path d={`M 10 ${FACADE.bottom} L 510 ${FACADE.bottom}`} {...stroke} variants={drawn(0.1, 0.6)} />
      <motion.path d={`M ${FACADE.left} ${FACADE.bottom} L ${FACADE.left} ${FACADE.top} L ${FACADE.right} ${FACADE.top} L ${FACADE.right} ${FACADE.bottom}`} fill="none" {...stroke} variants={drawn(0.2)} />
      <motion.rect {...WINDOW} fill="var(--color-paper-raised)" {...stroke} variants={drawn(0.45)} />
      <motion.rect {...DOOR} fill="var(--color-paper-sunken)" {...stroke} variants={drawn(0.55)} />
      <WindowCup />
      <Sara />
      <Awning />
    </svg>
  );
}

const CRUSH = { seconds: 0.75, impactAt: 0.5, squash: 0.3 };
const DOCUMENT_HEIGHT = 0.46;
const ROOF_TOP = AWNING.top / 620;
const impactSeconds = CRUSH.seconds * CRUSH.impactAt;

function documentOffset(documentBottom: number) {
  return `${((documentBottom - DOCUMENT_HEIGHT) / DOCUMENT_HEIGHT) * 100}%`;
}

function SaraPortrait({ crushed }: { crushed: boolean }) {
  const [available, setAvailable] = useState(true);
  if (!available) return null;
  return (
    <motion.div
      variants={dropsIn(0.2, 30)}
      className="mb-deck-rise size-deck-portrait overflow-hidden rounded-full shadow-lg"
    >
      <motion.img
        src="/sara.jpg"
        alt="Sara"
        onError={() => setAvailable(false)}
        initial={false}
        animate={crushed ? { filter: "grayscale(1)", x: [0, -10, 9, -6, 4, 0] } : { filter: "grayscale(0)", x: 0 }}
        transition={{ filter: { duration: 0.6, delay: impactSeconds }, x: { duration: 0.5, delay: impactSeconds } }}
        className="size-full object-cover"
      />
    </motion.div>
  );
}

type Phase = "meet" | "sued" | "damages" | "others" | "payouts";
const phases: Phase[] = ["meet", "sued", "damages", "others", "payouts"];
const showsField = (phase: Phase) => phase === "others" || phase === "payouts";

const lawsuitCount = facts.adaLawsuitsFiled2025.value;
const FIELD = { collapseSeconds: 1.2, handoffSeconds: 0.4, fillDelay: 1.5, fillSeconds: 2.4, flightSeconds: 3.8 };
/** One ease for the shop shrinking and the dot growing, so the handoff between them reads as a single motion. */
const easeShrink = [0.65, 0, 0.35, 1] as const;
const SHOP_IN_FIELD: FieldOrigin = sarasShopInField;

const crushedDocumentY = documentOffset(1 - CRUSH.squash * (1 - ROOF_TOP));

function documentMotion(phase: Phase) {
  if (phase === "meet") return { y: "-260%", opacity: 0 };
  if (phase === "sued") return { y: ["-260%", documentOffset(ROOF_TOP), crushedDocumentY], opacity: 1 };
  return { y: crushedDocumentY, opacity: 1 };
}

function documentTransition(phase: Phase): Transition {
  if (phase === "sued") return { y: { duration: CRUSH.seconds, times: [0, CRUSH.impactAt, 1], ease: ["easeIn", "easeOut"] }, opacity: { duration: 0.05 } };
  return { duration: 0 };
}

function storefrontMotion(phase: Phase) {
  if (phase === "meet") return { scaleY: 1, scaleX: 1 };
  return { scaleY: CRUSH.squash, scaleX: 1.12 };
}

function storefrontTransition(phase: Phase): Transition {
  if (phase === "sued") return { duration: CRUSH.seconds - impactSeconds, ease: "easeOut", delay: impactSeconds };
  return { duration: 0.45, ease: "easeOut" };
}

function useFieldFill(active: boolean) {
  const progress = useMotionValue(0);
  useEffect(() => {
    if (!active) {
      progress.jump(0);
      return;
    }
    const controls = animate(progress, 1, { duration: FIELD.fillSeconds, delay: FIELD.fillDelay, ease: [0.33, 0, 0.2, 1] });
    return () => controls.stop();
  }, [active, progress]);
  return progress;
}

function LawsuitDocument({ phase }: { phase: Phase }) {
  return (
    <motion.div
      aria-hidden
      initial={false}
      animate={documentMotion(phase)}
      transition={documentTransition(phase)}
      style={{ height: `${DOCUMENT_HEIGHT * 100}%` }}
      className="absolute inset-x-0 top-0 z-10 flex flex-col gap-deck-hairline bg-paper-raised px-deck-gap pt-deck-rise shadow-2xl"
    >
      <p className="font-display text-lede font-extrabold">Lawsuit</p>
      {[0.9, 0.7, 0.85, 0.5].map((width) => (
        <span key={width} className="h-deck-hairline bg-rule" style={{ width: `${width * 100}%` }} />
      ))}
    </motion.div>
  );
}

function MeetSara() {
  return (
    <>
      <h2 className="font-display text-headline font-extrabold">
        <MaskedLines lines={["Sara."]} delay={0.1} />
      </h2>
      <p className="mt-deck-rise font-display text-lede font-bold text-ink-muted">
        <MaskedLines lines={["She runs Happy Lemon,", "a boba shop in San Jose."]} delay={0.5} />
      </p>
    </>
  );
}

function SaraGotSued() {
  return (
    <>
      <h2 className="font-display text-headline font-extrabold">
        <MaskedLines lines={["Sara’s boba", "shop got sued."]} delay={impactSeconds} />
      </h2>
      <div className="mt-deck-rise">
        <FinePrint delay={impactSeconds + 0.6}>{facts.lawsuit.caption}</FinePrint>
      </div>
    </>
  );
}

function OtherBusinesses({ progress }: { progress: MotionValue<number> }) {
  return (
    <>
      <p className="font-display text-display font-extrabold figures-tabular">
        <CountFromProgress progress={progress} total={lawsuitCount} />
      </p>
      <p className="mt-deck-hairline font-display text-lede font-bold">
        <MaskedLines lines={["more businesses were", "sued in 2025"]} delay={FIELD.fillDelay} />
      </p>
      <div className="mt-deck-rise">
        <FinePrint delay={FIELD.fillDelay + 0.8}>{facts.adaLawsuitsFiled2025.source}</FinePrint>
      </div>
    </>
  );
}

function StoryCopy({ phase, fill }: { phase: Phase; fill: MotionValue<number> }) {
  if (phase === "meet") return <MeetSara />;
  if (phase === "sued") return <SaraGotSued />;
  if (phase === "damages") return <DamagesCopy waitedSeconds={exitTransition.duration} />;
  if (phase === "payouts") return <PayoutsCopy />;
  return <OtherBusinesses progress={fill} />;
}

function SaraShopScene({ phase }: { phase: Phase }) {
  const zoomedOut = showsField(phase);
  return (
    <motion.div
      initial={false}
      animate={zoomedOut ? { scale: 0.08, opacity: 0 } : { scale: 1, opacity: 1 }}
      transition={zoomedOut ? { scale: { duration: FIELD.collapseSeconds, ease: easeShrink }, opacity: { duration: FIELD.handoffSeconds, delay: FIELD.collapseSeconds - FIELD.handoffSeconds / 2 } } : { duration: 0.5 }}
      style={{ originX: SHOP_IN_FIELD.x, originY: SHOP_IN_FIELD.y }}
      className="absolute inset-0 flex justify-center"
    >
      <LawsuitDocument phase={phase} />
      <AnimatePresence>
        {phase === "damages" && (
          <motion.div key="receipt" initial="enter" animate="present" exit="exit" className="absolute inset-x-0 top-0 z-20 flex justify-center pt-deck-printer">
            <Receipt />
          </motion.div>
        )}
      </AnimatePresence>
      <motion.div initial={false} animate={storefrontMotion(phase)} transition={storefrontTransition(phase)} style={{ originY: 1 }} className="flex h-full justify-center">
        <Storefront />
      </motion.div>
    </motion.div>
  );
}

/**
 * The dot's flight, in fractions of the art box. It leaves the shop counterclockwise along an ellipse that swings
 * left over the middle of the slide, comes back around, and eases onto its place in the field over the last stretch.
 */
const FLIGHT = { radiusX: 0.42, radiusY: 0.24, startDegrees: 60, settleFrom: 0.72, popScale: 1.6 };
const startRadians = (FLIGHT.startDegrees * Math.PI) / 180;
const ORBIT_CENTER = {
  x: SHOP_IN_FIELD.x - FLIGHT.radiusX * Math.cos(startRadians),
  y: SHOP_IN_FIELD.y - FLIGHT.radiusY * Math.sin(startRadians),
};

const smoothstep = (value: number) => value * value * (3 - 2 * value);

function flightPoint(progress: number, landing: FieldOrigin): FieldOrigin {
  const angle = ((FLIGHT.startDegrees - progress * 360) * Math.PI) / 180;
  const orbit = { x: ORBIT_CENTER.x + FLIGHT.radiusX * Math.cos(angle), y: ORBIT_CENTER.y + FLIGHT.radiusY * Math.sin(angle) };
  const settle = smoothstep(Math.max(0, (progress - FLIGHT.settleFrom) / (1 - FLIGHT.settleFrom)));
  return { x: orbit.x + (landing.x - orbit.x) * settle, y: orbit.y + (landing.y - orbit.y) * settle };
}

function useLanding(active: boolean) {
  const [landed, setLanded] = useState(false);
  useEffect(() => {
    if (!active) return;
    const timer = window.setTimeout(() => setLanded(true), (FIELD.collapseSeconds - FIELD.handoffSeconds + FIELD.flightSeconds) * 1000);
    return () => {
      window.clearTimeout(timer);
      setLanded(false);
    };
  }, [active]);
  return active && landed;
}

function FlyingDot({ landing }: { landing: FieldOrigin }) {
  const progress = useMotionValue(0);
  useEffect(() => {
    const controls = animate(progress, 1, { duration: FIELD.flightSeconds, delay: FIELD.collapseSeconds - FIELD.handoffSeconds, ease: [0.45, 0, 0.55, 1] });
    return () => controls.stop();
  }, [progress]);
  const left = useTransform(progress, (value) => `${flightPoint(value, landing).x * 100}%`);
  const top = useTransform(progress, (value) => `${flightPoint(value, landing).y * 100}%`);
  const scale = useTransform(progress, [0, 0.05, 0.85, 1], [0, FLIGHT.popScale, FLIGHT.popScale, 1]);
  return <motion.span aria-hidden style={{ left, top, scale }} className="flying-dot absolute z-30 rounded-full bg-tape" />;
}

export function SaraSlide({ step }: SlideProps) {
  const phase = phases[Math.min(step, phases.length - 1)];
  const fieldShown = showsField(phase);
  const fill = useFieldFill(fieldShown);
  const landed = useLanding(fieldShown);
  const unpaidFade = useUnpaidFade(phase === "payouts");
  const [landing, setLanding] = useState<FieldOrigin>(SHOP_IN_FIELD);
  const placeLanding = useCallback((position: FieldOrigin) => setLanding(position), []);
  return (
    <div className="deck-gutter grid h-full grid-cols-[1.7fr_1fr] items-center gap-deck-gap">
      <div>
        {phase === "meet" || phase === "sued" ? <SaraPortrait crushed={phase === "sued"} /> : null}
        <AnimatePresence mode="wait" initial={false}>
          <motion.div key={phase} initial="enter" animate="present" exit="exit">
            <StoryCopy phase={phase} fill={fill} />
          </motion.div>
        </AnimatePresence>
      </div>
      <div className="relative h-deck-art">
        <motion.div
          initial={false}
          animate={{ opacity: fieldShown ? 1 : 0 }}
          transition={{ duration: 0.6, delay: phase === "others" ? FIELD.collapseSeconds : 0 }}
          className="absolute inset-0"
        >
          <DotField
            count={lawsuitFieldCount}
            progress={fill}
            origin={SHOP_IN_FIELD}
            showHighlight={landed}
            onHighlightPlaced={placeLanding}
            unpaidShare={payoutUnpaidShare}
            unpaidFade={unpaidFade}
          />
        </motion.div>
        {fieldShown && !landed && <FlyingDot landing={landing} />}
        {phase === "payouts" && <DrainingBills />}
        <SaraShopScene phase={phase} />
      </div>
    </div>
  );
}
