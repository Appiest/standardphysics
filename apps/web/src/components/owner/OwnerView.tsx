"use client";

import { ArrowLeft } from "@phosphor-icons/react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { useArrangement } from "@/components/workspace/useArrangement";
import { canBeCounter } from "@/lib/counter";
import { groupFindings } from "@/lib/findings";
import { proposeFix } from "@/lib/layout-client";
import { inApp, listenToApp, tellApp } from "@/lib/native-bridge";
import { markStatus, savePlan, walkingRoute } from "@/lib/owner-client";
import { type ChecklistStatus, checklistRows, type Destination, followUps, isFixing, type Panel, panelFor, pieceToTry, requestsForStep } from "@/lib/owner-journey";
import type { Assessment, Checklist, Finding, Journey, OwnerRequest, Scan, Scenario, SceneGraph, Vec3 } from "@/types/contracts";
import { CounterStep } from "./CounterStep";
import { OwnerModel } from "./OwnerModel";
import { PathStep } from "./PathStep";
import { PlanPanel } from "./PlanPanel";
import { RequestList } from "./RequestList";
import { ResultsPanel, type Row } from "./ResultsPanel";
import { SavePrompt } from "./SavePrompt";
import { SharePanel } from "./SharePanel";
import { StepHeading } from "./StepHeading";
import { StillToCheck } from "./StillToCheck";
import { ToolsPanel } from "./ToolsPanel";
import { guessCounter, useOwnerModel } from "./useOwnerModel";
import { usePathEditor } from "./usePathEditor";
import { WaitingPanel } from "./WaitingPanel";
import { DrivingPad, WheelchairPanel } from "./WheelchairPanel";

export type OwnerViewProps = {
  scan: Scan;
  journey: Journey;
  requests: OwnerRequest[];
  scene: SceneGraph | null;
  glbUrl: string | null;
  assessment: Assessment | null;
  checklist: Checklist;
  suggestedPath: Scenario | null;
  /** The path the owner confirmed, where the wheelchair walk-through starts. */
  scenario: Scenario | null;
  defaultPlaces: Destination[];
  guest: boolean;
  embedded: boolean;
  readOnly?: boolean;
  /** Shown under a read-only list, like the example shop's invitation to measure your own. */
  footer?: ReactNode;
};

/** Keeps the app's home card current, and hears from the app when a photo it took has uploaded. */
function useAppLink(scanId: string, stage: string) {
  const router = useRouter();
  useEffect(() => { tellApp({ type: "stageChanged", scanId, stage }); }, [scanId, stage]);
  useEffect(() => listenToApp({ photoSent: () => router.refresh() }), [router]);
}

export function OwnerView(props: OwnerViewProps) {
  useAppLink(props.scan.id, props.journey.stage);
  if (props.scene === null) {
    return (
      <Frame shopName={props.scan.name} embedded={props.embedded} model={null}>
        <EarlyPanel {...props} />
      </Frame>
    );
  }
  return <OwnerShop {...props} scene={props.scene} />;
}

const STEP_WHY: Record<string, string> = {
  answers: "Your answers tell us which doors and rooms to check.",
  photos: "We check each photo and add what we find to your results.",
};

/** The questions the app asks in the shop, and the measuring wait before the shop has a model. */
function EarlyPanel({ scan, journey, requests }: OwnerViewProps) {
  const asked = requestsForStep(journey, requests);
  if (panelFor(journey) !== "answers" || asked.length === 0) return <WaitingPanel journey={journey} />;
  const why = STEP_WHY[journey.next_step.kind] ?? STEP_WHY.answers;
  return (
    <div className="flex flex-col gap-6">
      <StepHeading title={journey.next_step.title}>{why}</StepHeading>
      <RequestList scanId={scan.id} requests={asked} />
    </div>
  );
}

const MODEL_HEIGHT: Record<ModelSize, string> = { small: "h-[30dvh] min-h-52", medium: "h-[42dvh] min-h-64", large: "h-[62dvh] min-h-64" };
type ModelSize = "small" | "medium" | "large";

/** How much of a phone the model takes: most while driving, some while tapping on it, least while reading a list. */
function modelSize(panel: string): ModelSize {
  if (panel === "wheelchair") return "large";
  return panel === "counter" || panel === "path" || panel === "plan" ? "medium" : "small";
}

function Frame({ shopName, embedded, model, size = "medium", step = "", children }: { shopName: string; embedded: boolean; model: ReactNode; size?: ModelSize; step?: string; children: ReactNode }) {
  return (
    <div className={`grid h-dvh grid-cols-[minmax(0,1fr)] overflow-hidden ${model ? "grid-rows-[auto_auto_minmax(0,1fr)] lg:grid-cols-[minmax(0,1fr)_28rem] lg:grid-rows-[auto_minmax(0,1fr)]" : "grid-rows-[auto_minmax(0,1fr)]"}`}>
      {embedded ? <span /> : <Header shopName={shopName} wide={model !== null} />}
      {model && <section aria-label="Your shop in 3D" className={`relative ${MODEL_HEIGHT[size]} touch-none overflow-hidden lg:h-auto lg:rounded-tr-2xl`}>{model}</section>}
      <main key={step} className="min-h-0 overflow-y-auto overscroll-contain px-4 pt-6 lg:px-6 lg:pt-8">
        <div className="mx-auto flex min-h-full max-w-xl flex-col gap-8 pb-6">{children}</div>
      </main>
    </div>
  );
}

function Header({ shopName, wide }: { shopName: string; wide: boolean }) {
  return (
    <header className={`flex items-center gap-2 px-2 py-2 ${wide ? "lg:col-span-2" : ""}`}>
      <Link href="/" className="grid size-11 place-items-center rounded-lg text-ink-muted hover:bg-ink/5 hover:text-ink" aria-label="Your shops">
        <ArrowLeft size={20} weight="bold" aria-hidden />
      </Link>
      <p className="truncate text-lg font-semibold">{shopName}</p>
    </header>
  );
}

type ShopProps = OwnerViewProps & { scene: SceneGraph };


/** The checklist statuses as the owner sets them, shown at once and saved behind the scenes. */
function useStatuses(scanId: string, guest: boolean, onFirstGuestMark: () => void) {
  const router = useRouter();
  const [overrides, setOverrides] = useState<Record<string, ChecklistStatus>>({});
  const [saving, setSaving] = useState(false);
  const set = useCallback(async (finding: Finding, status: ChecklistStatus) => {
    setOverrides((current) => ({ ...current, [finding.id]: status }));
    setSaving(true);
    try {
      await markStatus(scanId, finding.id, status);
      router.refresh();
      if (guest) onFirstGuestMark();
    } finally {
      setSaving(false);
    }
  }, [scanId, guest, onFirstGuestMark, router]);
  return { overrides, saving, set };
}

function useSaveAsk(guest: boolean) {
  const [asked, setAsked] = useState(false);
  const [open, setOpen] = useState(false);
  const ask = useCallback(() => {
    if (!guest || asked) return;
    setAsked(true);
    setOpen(true);
  }, [guest, asked]);
  return { open, ask, close: () => setOpen(false) };
}

type Tool = "plan" | "wheelchair";

function pieceLabel(scene: SceneGraph, nodeId: string | null): string | null {
  return scene.nodes.find((node) => node.id === nodeId)?.label ?? null;
}

function currentPanel(journey: Journey, counterSkipped: boolean, tool: Tool | null, readOnly: boolean): Panel | Tool {
  if (readOnly) return "results";
  if (tool) return tool;
  const panel = panelFor(journey);
  return panel === "counter" && counterSkipped ? "path" : panel;
}

function OwnerShop(props: ShopProps) {
  const { scan, scene, journey, assessment, checklist, guest, readOnly = false } = props;
  const [selected, setSelected] = useState<Finding | null>(null);
  const [counter, setCounter] = useState<string | null>(() => guessCounter(scene));
  const [counterSkipped, setCounterSkipped] = useState(false);
  const [tool, setTool] = useState<Tool | null>(null);
  const [walkedLegs, setWalkedLegs] = useState<Vec3[][]>([]);
  const [fixingHere, setFixingHere] = useState(false);
  const save = useSaveAsk(guest);
  const statuses = useStatuses(scan.id, guest, save.ask);
  const path = usePathEditor(scan.id, props.suggestedPath, props.defaultPlaces);
  const arrangement = useArrangement(scan.id, scene, savePlan);
  const panel = currentPanel(journey, counterSkipped, tool, readOnly);
  const groups = useMemo(() => groupFindings(assessment?.findings ?? []), [assessment]);
  const problems = groups.problems;
  const rows: Row[] = useMemo(
    () => checklistRows(problems, checklist).map((row) => ({ ...row, status: statuses.overrides[row.finding.id] ?? row.status })),
    [problems, checklist, statuses.overrides],
  );

  const pickNode = useCallback((nodeId: string) => {
    const node = scene.nodes.find((candidate) => candidate.id === nodeId);
    if (panel === "counter" && node && canBeCounter(node)) setCounter(nodeId);
  }, [panel, scene]);
  const setup = useOwnerModel(
    {
      panel: panel === "plan" || panel === "wheelchair" ? "results" : panel, scene, selected, counter,
      path: panel === "path" ? path : null, arrangement: panel === "plan" ? arrangement : null, wheelchair: panel === "wheelchair",
      scenario: props.scenario, walkedLegs,
    },
    pickNode,
    () => setSelected(null),
  );

  const tryPiece = useMemo(() => pieceToTry(problems, scene), [problems, scene]);
  const startWheelchair = () => {
    setTool("wheelchair");
    if (props.scenario) {
      walkingRoute(scan.id, props.scenario).then((walked) => setWalkedLegs(walked.legs.map((leg) => leg.path))).catch(() => setWalkedLegs([]));
    }
  };
  const startPlanning = () => {
    setTool("plan");
    if (tryPiece) arrangement.setActiveId(tryPiece.id);
  };

  const planFor = async (finding: Finding) => {
    setTool("plan");
    setSelected(null);
    const result = await proposeFix(scan.id, scene.revision, [finding.id]).catch(() => null);
    if (result?.proposal) arrangement.load(result.proposal.moves);
  };

  const content: Record<Panel | Tool, () => ReactNode> = {
    waiting: () => <WaitingPanel journey={journey} />,
    failed: () => <WaitingPanel journey={journey} />,
    answers: () => <EarlyPanel {...props} />,
    counter: () => <CounterStep scanId={scan.id} scene={scene} picked={counter} onSkip={() => setCounterSkipped(true)} />,
    path: () => <PathStep path={path} />,
    follow_ups: () => <FollowUpPanel scanId={scan.id} journey={journey} requests={props.requests} />,
    plan: () => <PlanPanel arrangement={arrangement} before={problems.length} pieceName={pieceLabel(scene, arrangement.activeId) ?? tryPiece?.label ?? null} onDone={() => { arrangement.reset(); setTool(null); }} />,
    wheelchair: () => <WheelchairPanel onDone={() => setTool(null)} />,
    results: () => (
      <ResultsPanel
        rows={rows}
        scene={scene}
        selectedId={selected?.id ?? null}
        fixing={isFixing(checklist, fixingHere) && !readOnly}
        saving={statuses.saving}
        readOnly={readOnly}
        onStartFixing={() => setFixingHere(true)}
        footer={props.footer}
        stillToCheck={<StillToCheck scanId={scan.id} questions={readOnly ? [] : groups.questions} requests={props.requests} />}
        pending={groups.questions.length}
        actions={{ onShow: (finding) => setSelected(finding.id === selected?.id ? null : finding), onStatus: statuses.set, onPlan: planFor }}
      >
        {!readOnly && <SharePanel scanId={scan.id} shopName={scan.name} onShared={save.ask} />}
        {!readOnly && journey.tools_unlocked && <ToolsPanel scanId={scan.id} inApp={inApp()} onPlan={startPlanning} onWheelchair={startWheelchair} />}
      </ResultsPanel>
    ),
  };

  return (
    <Frame shopName={scan.name} embedded={props.embedded} model={<><OwnerModel scene={scene} glbUrl={props.glbUrl} setup={setup} lightweight={props.embedded} />{panel === "wheelchair" && <DrivingPad />}</>} size={modelSize(panel)} step={panel}>
      {content[panel]()}
      <SavePrompt open={save.open} inApp={props.embedded} onClose={save.close} />
    </Frame>
  );
}

function FollowUpPanel({ scanId, journey, requests }: { scanId: string; journey: Journey; requests: OwnerRequest[] }) {
  const all = followUps(requests);
  const answerable = all.filter((request) => request.kind !== "another_look");
  const lookAgain = all.filter((request) => request.kind === "another_look");
  return (
    <div className="flex flex-col gap-6">
      <StepHeading title={journey.next_step.title}>Your shop is measured. One more thing and we can finish checking it.</StepHeading>
      <RequestList scanId={scanId} requests={answerable} />
      {lookAgain.length > 0 && (
        <section className="flex flex-col gap-2">
          <h2 className="text-lg font-semibold">Needs another look</h2>
          <p className="text-pretty text-ink-muted">The next time you walk the shop, go slowly past these.</p>
          <ul className="list-disc pl-5">{lookAgain.map((request) => <li key={request.id}>{request.title}</li>)}</ul>
        </section>
      )}
    </div>
  );
}
