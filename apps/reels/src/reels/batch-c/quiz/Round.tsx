import { Check, X } from "@phosphor-icons/react";
import { useCurrentFrame } from "remotion";
import { FinePrint } from "../../../components/FinePrint";
import { RevealLines } from "../../../components/Type";
import { drawn, progress, snap } from "../../../lib/ease";

export type Question = { lines: string[]; options: string[]; answer: number; rule: string; source: string };

export const ROUND = { optionsAt: 10, countdownAt: 34, countdownFrames: 60, revealAt: 96, length: 156 } as const;

const LETTERS = ["A", "B", "C"];

function optionState(frame: number, index: number, answer: number) {
  const revealed = progress(frame, ROUND.revealAt, 10, snap);
  return { revealed, correct: index === answer };
}

function Option({ label, index, answer }: { label: string; index: number; answer: number }) {
  const frame = useCurrentFrame();
  const enter = progress(frame, ROUND.optionsAt + index * 5, 12, drawn);
  const { revealed, correct } = optionState(frame, index, answer);
  const pop = correct ? 1 + 0.08 * Math.sin(Math.min(1, revealed) * Math.PI) : 1 - 0.04 * revealed;
  const Icon = correct ? Check : X;
  return (
    <div
      className={`flex h-[170px] items-center gap-8 px-10 shadow-[0_18px_40px_rgba(13,13,12,0.18)] ${correct && revealed > 0.5 ? "bg-ink text-paper-raised ring-8 ring-tape/60" : "bg-paper-raised text-ink"}`}
      style={{ opacity: enter * (correct ? 1 : 1 - 0.65 * revealed), transform: `translateX(${(1 - enter) * 200}px) scale(${pop})` }}
    >
      <span className="reel-copy text-title text-ink-faint">{LETTERS[index]}</span>
      <span className="reel-copy figures flex-1 text-headline">{label}</span>
      {revealed > 0.5 && <Icon size={96} weight="bold" className={correct ? "text-tape" : "text-fail"} />}
    </div>
  );
}

function Countdown() {
  const frame = useCurrentFrame();
  const elapsed = frame - ROUND.countdownAt;
  if (elapsed < 0 || frame >= ROUND.revealAt) return null;
  const left = 1 - elapsed / ROUND.countdownFrames;
  const number = Math.max(1, Math.ceil(left * 3));
  const circumference = 2 * Math.PI * 88;
  return (
    <div className="absolute left-1/2 top-[1360px] flex size-[240px] -translate-x-1/2 items-center justify-center">
      <svg viewBox="0 0 200 200" className="absolute inset-0 -rotate-90">
        <circle cx={100} cy={100} r={88} fill="none" stroke="#c6c6c1" strokeWidth={14} />
        <circle cx={100} cy={100} r={88} fill="none" stroke="#f6be1a" strokeWidth={14} strokeDasharray={circumference} strokeDashoffset={circumference * (1 - left)} style={{ filter: "drop-shadow(0 0 12px #f6be1a)" }} />
      </svg>
      <span className="reel-copy figures text-poster">{number}</span>
    </div>
  );
}

function Answer({ question }: { question: Question }) {
  const frame = useCurrentFrame();
  const shown = progress(frame, ROUND.revealAt + 8, 14, drawn);
  return (
    <div className="absolute inset-x-safe-side top-[1360px]" style={{ opacity: shown, transform: `translateY(${(1 - shown) * 30}px)` }}>
      <p className="reel-caption text-caption">{question.rule}</p>
      <FinePrint at={ROUND.revealAt + 12} className="mt-3">
        {question.source}
      </FinePrint>
    </div>
  );
}

/** One quiz round: the question, three options sliding in, a three-second countdown, then the answer lit and the rest knocked back. */
export function Round({ question, number }: { question: Question; number: number }) {
  return (
    <div className="absolute inset-0">
      <div className="absolute inset-x-safe-side top-safe-top">
        <p className="reel-caption figures text-caption text-ink-muted">Round {number} of 3</p>
        <RevealLines lines={question.lines} at={0} stagger={2} className="reel-copy block text-title" />
      </div>
      <div className="absolute inset-x-safe-side top-[800px] flex flex-col gap-8">
        {question.options.map((label, index) => (
          <Option key={label} label={label} index={index} answer={question.answer} />
        ))}
      </div>
      <Countdown />
      <Answer question={question} />
    </div>
  );
}
