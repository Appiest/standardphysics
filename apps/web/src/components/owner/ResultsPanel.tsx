"use client";

import { CaretLeft, CaretRight } from "@phosphor-icons/react";
import { AnimatePresence, MotionConfig, motion } from "motion/react";
import { type ReactNode, useEffect, useMemo, useRef } from "react";
import { Button } from "@/components/ui/Button";
import type { ChecklistStatus } from "@/lib/owner-journey";
import { easeDrawn } from "@/lib/motion";
import { useSeenOnce } from "@/lib/seen-once";
import type { Finding, SceneGraph } from "@/types/contracts";
import { type CardActions, FindingCard } from "./FindingCard";
import { ActionBar } from "./StepHeading";

export type Row = { finding: Finding; status: ChecklistStatus };

/** The three places the results page opens into; null is the page that lists them. */
export type ResultsSection = "fix" | "check" | "share" | null;

type Props = {
  rows: Row[];
  scene: SceneGraph | null;
  selectedId: string | null;
  fixing: boolean;
  saving: boolean;
  actions: CardActions;
  onStartFixing: () => void;
  readOnly: boolean;
  footer?: ReactNode;
  /** The questions the checks couldn't settle yet. */
  stillToCheck?: ReactNode;
  /** How many of those there are, so a shop with none to fix isn't called clear while some wait. */
  pending: number;
  /** The whole-room fix, shown above the list when a layout model is set up; it replaces the "Start fixing" bar. */
  fixRoom?: ReactNode;
  section: ResultsSection;
  onSection: (section: ResultsSection) => void;
  /** Sharing the report and the shop tools. */
  children?: ReactNode;
};

const RESULTS_TIP = "sp_results_tip";
const SLIDE = { duration: 0.22, ease: easeDrawn };

/**
 * The results open like folders: a short page listing what to fix, what is
 * still to check, and the sharing and shop tools, each entered on its own.
 * A read-only report has only the list, so it shows the list directly.
 */
export function ResultsPanel(props: Props) {
  const { readOnly, section, footer } = props;
  const top = useScrollToTopOn(section);
  if (readOnly) {
    return <div className="flex min-h-full flex-col gap-6"><FixSection {...props} />{footer}</div>;
  }
  const enter = section ? 24 : -24;
  return (
    <MotionConfig reducedMotion="user">
      <div ref={top} className="flex min-h-full flex-col">
        <AnimatePresence mode="wait" initial={false}>
          <motion.div key={section ?? "home"} className="flex flex-1 flex-col gap-6"
            initial={{ opacity: 0, x: enter }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -enter }} transition={SLIDE}>
            {section ? <OpenFolder {...props} section={section} /> : <Home {...props} />}
          </motion.div>
        </AnimatePresence>
      </div>
    </MotionConfig>
  );
}

function OpenFolder(props: Props & { section: NonNullable<ResultsSection> }) {
  const back = () => props.onSection(null);
  const folders: Record<NonNullable<ResultsSection>, () => ReactNode> = {
    fix: () => <Folder onBack={back}><FixSection {...props} /></Folder>,
    check: () => <Folder title="Still to check" onBack={back}>{props.stillToCheck}</Folder>,
    share: () => <Folder title="Share and tools" onBack={back}>{props.children}</Folder>,
  };
  return folders[props.section]();
}

/** Entering or leaving a folder starts at its top, not wherever the last page was scrolled to. */
function useScrollToTopOn(section: ResultsSection) {
  const top = useRef<HTMLDivElement>(null);
  useEffect(() => { top.current?.closest("main")?.scrollTo({ top: 0 }); }, [section]);
  return top;
}

function Home({ rows, fixing, pending, onSection, footer, children }: Props) {
  const toDo = rows.filter((row) => row.status === "to_do").length;
  return (
    <>
      {fixing ? <Progress rows={rows} /> : <Count count={rows.length} pending={pending} />}
      <nav aria-label="Results" className="flex flex-col gap-3">
        {rows.length > 0 && (
          <FolderLink title="Things to fix" fact={fixing ? stillToDo(toDo) : preview(rows)} count={fixing ? toDo : rows.length} urgent onOpen={() => onSection("fix")} />
        )}
        {pending > 0 && (
          <FolderLink title="Still to check" fact={`${pending === 1 ? "A question" : `${pending} questions`} the scan couldn't answer on its own.`} count={pending} onOpen={() => onSection("check")} />
        )}
        {children && <FolderLink title="Share and tools" fact="Send the report to a contractor, or try moving furniture around." onOpen={() => onSection("share")} />}
      </nav>
      {footer}
    </>
  );
}

function stillToDo(count: number): string {
  return count === 0 ? "Every item has a status." : `${count} still to do.`;
}

/** The first problem by name, so the folder says what is inside before it opens. */
function preview(rows: Row[]): string {
  const [first, ...rest] = rows;
  return rest.length === 0 ? first.finding.title : `${first.finding.title}, and ${rest.length} more.`;
}

function FolderLink({ title, fact, count, urgent = false, onOpen }: { title: string; fact: string; count?: number; urgent?: boolean; onOpen: () => void }) {
  return (
    <button type="button" onClick={onOpen}
      className="group grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 rounded-2xl bg-sheet p-4 text-start shadow-float pressable-wide hover:bg-ink/[0.03]">
      <span className="text-lg font-semibold">{title}</span>
      <span className="row-span-2 flex items-center gap-2">
        {count !== undefined && count > 0 && (
          <span className={`measurement grid min-w-8 place-items-center rounded-full px-2 py-0.5 text-sm ${urgent ? "bg-problem/10 text-problem" : "bg-ink/[0.06] text-ink-muted"}`}>{count}</span>
        )}
        <CaretRight size={18} weight="bold" className="text-ink-muted transition-transform duration-150 group-hover:translate-x-0.5" aria-hidden />
      </span>
      <span className="line-clamp-2 text-pretty text-sm text-ink-muted">{fact}</span>
    </button>
  );
}

function Folder({ title, onBack, children }: { title?: string; onBack: () => void; children: ReactNode }) {
  return (
    <>
      <Button className="-ms-3 self-start" onClick={onBack}>
        <CaretLeft size={16} weight="bold" aria-hidden />
        All results
      </Button>
      {title && <h1 className="heading-display -mt-3 text-3xl">{title}</h1>}
      {children}
    </>
  );
}

/** The checklist itself. Before the owner starts fixing it counts what to fix; after, each card carries its status. */
function FixSection({ rows, scene, selectedId, fixing, saving, actions, onStartFixing, readOnly, pending, fixRoom }: Props) {
  const [tipSeen, markTipSeen] = useSeenOnce(RESULTS_TIP);
  const cardActions = useMemo<CardActions>(() => ({
    ...actions,
    onShow: (finding) => { markTipSeen(); actions.onShow(finding); },
    onStatus: (finding, status) => { markTipSeen(); actions.onStatus(finding, status); },
  }), [actions, markTipSeen]);
  const offerFix = offersFix(rows, fixing, readOnly);
  return (
    <>
      {fixing ? <Progress rows={rows} /> : <Count count={rows.length} pending={pending} />}
      {rows.length > 0 && !tipSeen && <FirstResultsTip onDismiss={markTipSeen} />}
      {offerFix && fixRoom}
      <ul className="flex flex-col gap-4">
        {rows.map(({ finding, status }) => (
          <li key={finding.id}>
            <FindingCard finding={finding} scene={scene} selected={finding.id === selectedId} status={status}
              fixing={fixing && !readOnly} saving={saving} readOnly={readOnly} actions={cardActions} />
          </li>
        ))}
      </ul>
      {offerFix && !fixRoom && (
        <ActionBar>
          <Button variant="primary" className="justify-center" onClick={onStartFixing}>Start fixing</Button>
        </ActionBar>
      )}
    </>
  );
}

/** Whether the owner can still start fixing: there is something to fix and they haven't started. */
function offersFix(rows: Row[], fixing: boolean, readOnly: boolean): boolean {
  return !fixing && !readOnly && rows.length > 0;
}

/** One heading that says what it counts. The number carries the red, and nothing else does. */
function Count({ count, pending }: { count: number; pending: number }) {
  if (count === 0) {
    return <h1 className="heading-display text-3xl">{pending === 0 ? "Nothing to fix" : "Nothing to fix so far"}</h1>;
  }
  return (
    <h1 className="heading-display text-3xl">
      <span className="text-problem">{count}</span> {count === 1 ? "thing to fix" : "things to fix"}
    </h1>
  );
}

/** How far through the list the owner is: one segment per item, filled once it has a status. */
function Progress({ rows }: { rows: Row[] }) {
  const done = rows.filter((row) => row.status !== "to_do").length;
  return (
    <header className="flex flex-col gap-3">
      <h1 className="heading-display text-3xl">{done} of {rows.length} done</h1>
      <div className="flex gap-1" aria-hidden>
        {rows.map(({ finding, status }) => (
          <span key={finding.id} className={`h-1.5 flex-1 rounded-full transition-colors duration-300 ${status === "to_do" ? "bg-ink/10" : "bg-pass"}`} />
        ))}
      </div>
    </header>
  );
}

function FirstResultsTip({ onDismiss }: { onDismiss: () => void }) {
  return (
    <aside className="flex items-start gap-3 rounded-2xl bg-ink p-4 text-paper">
      <p className="flex-1 text-pretty">A red bar is how far a spot misses the ADA number. Tap a card to see where it is.</p>
      <Button variant="inverse" className="shrink-0" onClick={onDismiss}>Got it</Button>
    </aside>
  );
}
