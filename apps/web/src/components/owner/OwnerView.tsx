"use client";

import { ArrowLeft } from "@phosphor-icons/react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type ComponentProps, type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { useProposalReview } from "@/components/proposal/useProposalReview";
import { useArrangement } from "@/components/workspace/useArrangement";
import { canBeCounter } from "@/lib/counter";
import { groupFindings } from "@/lib/findings";
import { inApp, listenToApp, tellApp } from "@/lib/native-bridge";
import { latestPlan, markStatus, savePlan, walkingRoute } from "@/lib/owner-client";
import { statusLookup } from "@/lib/owner-decisions";
import { type ChecklistStatus, checklistRows, type Destination, isFixing, type Panel, panelFor, pieceToTry, requestsForStep } from "@/lib/owner-journey";
import type { Assessment, Checklist, Finding, Journey, LayoutCheckResult, NodeMove, OwnerRequest, ProposalResult, Scan, Scenario, SceneGraph, SceneNode, Vec3 } from "@/types/contracts";
import { AllClearSheet } from "./AllClearSheet";
import { CounterStep } from "./CounterStep";
import { OwnerModel } from "./OwnerModel";
import { PathStep } from "./PathStep";
import { PlanPanel } from "./PlanPanel";
import { PlanReview } from "./PlanReview";
import { RequestList } from "./RequestList";
import { ResultsPanel, type ResultsSection, type Row } from "./ResultsPanel";
import { SavePrompt } from "./SavePrompt";
import { SharePanel } from "./SharePanel";
import { StepHeading } from "./StepHeading";
import { StillToCheck } from "./StillToCheck";
import { stillToCheckCount, stillToCheckItems } from "@/lib/still-to-check";
import { FixAll, useModelLabel } from "./FixAll";
import { ToolsPanel } from "./ToolsPanel";
import { FoundLegend, FoundSection } from "./FoundList";
import { usePieceEditing } from "./useFoundEdits";
import { LayoutStage } from "./LayoutStage";
import { type TryLayout, useTryLayout } from "./useTryLayout";
import { type FoundObjects, foundInModel, pointedInModel, showsFound, useFoundObjects } from "./useFoundObjects";
import { guessCounter, useOwnerModel } from "./useOwnerModel";
import { usePathEditor } from "./usePathEditor";
import { type StaffAdjuster, useStaffAdjuster } from "./useStaffAdjuster";
import { WaitingPanel } from "./WaitingPanel";
import { DeleteScanButton } from "@/components/workspace/DeleteScanButton";
import { DrivingPad, WheelchairPanel } from "./WheelchairPanel";

export type OwnerViewProps = {
  scan: Scan;
  journey: Journey;
  requests: OwnerRequest[];
  scene: SceneGraph | null;
  glbUrl: string | null;
  /** The painted scan of the shop, when its photos have been baked onto it. */
  scanGlbUrl?: string | null;
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
      <Frame shopName={props.scan.name} embedded={props.embedded} model={null} end={shopEnd(props)}>
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

const MODEL_HEIGHT: Record<ModelSize, string> = { small: "h-[30dvh] min-h-52", medium: "h-[42dvh] min-h-64", plan: "h-[52dvh] min-h-64", large: "h-[62dvh] min-h-64" };
type ModelSize = "small" | "medium" | "plan" | "large";

/** How much of a phone the model takes: most while driving or dragging furniture, some while tapping on it, least while reading a list. */
function modelSize(panel: string): ModelSize {
  if (panel === "wheelchair") return "large";
  if (panel === "plan") return "plan";
  return panel === "counter" || panel === "path" ? "medium" : "small";
}

/** Where the owner deletes a shop: the end of every step's page, and never on a shop they can only read. */
function shopEnd(props: OwnerViewProps): ReactNode {
  if (props.readOnly) return null;
  return <DeleteScanButton look="page" scanId={props.scan.id} name={props.scan.name} />;
}

function Frame({ shopName, embedded, model, size = "medium", step = "", end, children }: { shopName: string; embedded: boolean; model: ReactNode; size?: ModelSize; step?: string; end: ReactNode; children: ReactNode }) {
  return (
    <div className={`grid h-dvh grid-cols-[minmax(0,1fr)] overflow-hidden ${model ? "grid-rows-[auto_auto_minmax(0,1fr)] lg:grid-cols-[minmax(0,1fr)_28rem] lg:grid-rows-[auto_minmax(0,1fr)]" : "grid-rows-[auto_minmax(0,1fr)]"}`}>
      {embedded ? <span /> : <Header shopName={shopName} wide={model !== null} />}
      {model && <section aria-label="Your shop in 3D" className={`relative ${MODEL_HEIGHT[size]} touch-none overflow-hidden lg:h-auto lg:rounded-tr-2xl`}>{model}</section>}
      <main key={step} className="min-h-0 overflow-y-auto overscroll-contain px-4 pt-6 lg:px-6 lg:pt-8">
        <div className="mx-auto flex min-h-full max-w-xl flex-col gap-8 pb-6">
          {children}
          {end}
        </div>
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

/** The offer made when nothing is left to fix: keep the layout and open the report, or stay in the room. */
function useAllClearSheet(scanId: string, arrangement: ReturnType<typeof useArrangement>) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [opening, setOpening] = useState(false);
  const show = useCallback(() => setOpen(true), []);
  const seeReport = async () => {
    setOpening(true);
    if (arrangement.hasMoves && !arrangement.saved) await arrangement.save();
    router.push(`/scans/${scanId}/report`);
  };
  return { open, opening, show, seeReport, close: () => setOpen(false) };
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

function isBuiltIn(scene: SceneGraph, nodeId: string | null): boolean {
  const node = scene.nodes.find((candidate) => candidate.id === nodeId);
  return node !== undefined && node.kind === "object" && !node.movable;
}

function pieceLabel(scene: SceneGraph, nodeId: string | null): string | null {
  return scene.nodes.find((node) => node.id === nodeId)?.label ?? null;
}

/** The whole-room fix when a layout model is set up; null sends the owner to the one-at-a-time checklist instead. */
function fixRoomCard(label: string | null, card: Omit<ComponentProps<typeof FixAll>, "label">): ReactNode {
  return label ? <FixAll key={card.revision} {...card} label={label} /> : null;
}

/** The same loop started from the owner's plan; keyed by the plan so each new arrangement gets a fresh card. */
function fixPlanCard(label: string | null, card: Omit<ComponentProps<typeof FixAll>, "label">): ReactNode {
  return label ? <FixAll key={`${card.revision}:${planKey(card.plan ?? [])}`} {...card} label={label} /> : null;
}

/**
 * The pieces a fix run proposed moving, outlined on the plan while the layout it
 * loaded stands; the first move the owner makes on their own lets go of them.
 */
function useProposedPieces(arrangement: ReturnType<typeof useArrangement>) {
  const [proposal, setProposal] = useState<{ key: string; ids: Set<string> } | null>(null);
  const current = planKey(Object.values(arrangement.moves));
  const ids = proposal?.key === current ? proposal.ids : NOTHING_PROPOSED;
  const show = (moves: NodeMove[], proposed: string[]) => setProposal({ key: planKey(moves), ids: new Set(proposed) });
  return { ids, show };
}

const NOTHING_PROPOSED = new Set<string>();

function planKey(plan: NodeMove[]): string {
  return plan.map((move) => `${move.node_id}:${move.delta_translation.x.toFixed(3)},${move.delta_translation.y.toFixed(3)},${move.delta_rotation_z_degrees.toFixed(1)}`).sort().join("|");
}

function currentPanel(journey: Journey, counterSkipped: boolean, tool: Tool | null, readOnly: boolean): Panel | Tool {
  if (readOnly) return "results";
  if (tool) return tool;
  const panel = panelFor(journey);
  return panel === "counter" && counterSkipped ? "path" : panel;
}

/** Tapping a piece in the model: it becomes the counter while choosing one, and picks its found row where those show. */
function useNodePicker(panel: Panel | Tool, scene: SceneGraph, foundShown: boolean, pickFoundNode: (nodeId: string) => void, setCounter: (nodeId: string) => void) {
  return useCallback((nodeId: string) => {
    if (panel !== "counter") {
      if (foundShown) pickFoundNode(nodeId);
      return;
    }
    const node = scene.nodes.find((candidate) => candidate.id === nodeId);
    if (node && canBeCounter(node)) setCounter(nodeId);
  }, [panel, scene, foundShown, pickFoundNode, setCounter]);
}

/** The tools draw on the results view's model: planning moves pieces, the walk-through drives through them. */
function modelPanel(panel: Panel | Tool): Panel {
  return panel === "plan" || panel === "wheelchair" ? "results" : panel;
}

/** The two tools, started from the results: planning opens on the last saved plan or the piece most worth moving, the walk-through fetches its route. */
function useTools(scanId: string, revision: number, scenario: Scenario | null, arrangement: ReturnType<typeof useArrangement>, tryPiece: SceneNode | null) {
  const [tool, setTool] = useState<Tool | null>(null);
  const [walkedLegs, setWalkedLegs] = useState<Vec3[][]>([]);
  const startWheelchair = () => {
    setTool("wheelchair");
    if (!scenario) return;
    walkingRoute(scanId, scenario).then((walked) => setWalkedLegs(walked.legs.map((leg) => leg.path))).catch(() => setWalkedLegs([]));
  };
  const startPlanning = () => {
    setTool("plan");
    arrangement.start();
    if (tryPiece) arrangement.setActiveId(tryPiece.id);
    latestPlan(scanId, revision).then((plan) => plan && arrangement.restore(plan.moves)).catch(() => {});
  };
  return { tool, setTool, walkedLegs, startWheelchair, startPlanning };
}

function OwnerShop(props: ShopProps) {
  const { scan, scene, journey, assessment, guest, readOnly = false } = props;
  const [selected, setSelected] = useState<Finding | null>(null);
  const [counter, setCounter] = useState<string | null>(() => guessCounter(scene));
  const [counterSkipped, setCounterSkipped] = useState(false);
  const [fixingHere, setFixingHere] = useState(false);
  const [planFinding, setPlanFinding] = useState<Finding | null>(null);
  const [resultsSection, setResultsSection] = useState<ResultsSection>(null);
  const review = useProposalReview(scan.id, scene.revision, scan.owner_wishes);
  const modelLabel = useModelLabel();
  const save = useSaveAsk(guest);
  const statuses = useStatuses(scan.id, guest, save.ask);
  const path = usePathEditor(scan.id, props.suggestedPath, props.defaultPlaces);
  const staff = useStaffAdjuster(scan.id, props.scenario);
  const arrangement = useArrangement(scan.id, scene, savePlan, "keep");
  const groups = useMemo(() => groupFindings(assessment?.findings ?? []), [assessment]);
  const problems = groups.problems;
  const tryPiece = useMemo(() => pieceToTry(problems, scene), [problems, scene]);
  const tools = useTools(scan.id, scene.revision, props.scenario, arrangement, tryPiece);
  const proposed = useProposedPieces(arrangement);
  const panel = currentPanel(journey, counterSkipped, tools.tool, readOnly);
  const letGoOfFinding = useCallback(() => setSelected(null), []);
  const { trying, found, trial, scanned } = useTrying(panel, arrangement, scene, assessment, letGoOfFinding);
  const editing = usePieceEditing(found, scan.id, scene.revision, { readOnly, trying });
  const foundShown = showsFound(panel);
  const pickNode = useNodePicker(panel, scene, foundShown, found.pickNode, setCounter);
  const setup = useOwnerModel(
    {
      panel: modelPanel(panel), scene, selected, counter,
      path: panel === "path" ? path : null, arrangement: panel === "plan" ? arrangement : null, wheelchair: panel === "wheelchair",
      scenario: props.scenario, staff: staffToChange(staff, panel, readOnly), walkedLegs: tools.walkedLegs, ...foundForModel(found, trying, foundShown),
    },
    pickNode,
    () => { setSelected(null); found.clear(); },
  );

  const showProposal = (result: ProposalResult) => {
    if (result.proposal) arrangement.load(result.proposal.moves);
    else arrangement.reset();
  };
  const planFor = (finding: Finding) => {
    tools.setTool("plan");
    setSelected(null);
    arrangement.start();
    setPlanFinding(finding);
    review.propose(finding.id, showProposal);
  };
  const showFixedLayout = (moves: NodeMove[], proposedIds: string[], check: LayoutCheckResult | null) => {
    arrangement.load(moves, check);
    arrangement.setActiveId(null);
    proposed.show(moves, proposedIds);
  };
  const openFixedLayout = (moves: NodeMove[], proposedIds: string[], check: LayoutCheckResult | null) => {
    tools.setTool("plan");
    setSelected(null);
    arrangement.start();
    showFixedLayout(moves, proposedIds, check);
  };
  const putItAllBack = () => {
    arrangement.reset();
    trial.clearFixedNote();
    review.clear();
  };
  /** "Put back" in the plan: every piece where it was scanned, as a step undo can take back, and no suggestion left under review. */
  const putEverythingBack = () => {
    arrangement.putBack();
    trial.clearFixedNote();
    review.clear();
  };
  const leavePlan = () => {
    putItAllBack();
    setPlanFinding(null);
    tools.setTool(null);
  };
  const show = (finding: Finding) => {
    found.clear();
    setSelected(finding.id === selected?.id ? null : finding);
  };

  const statusOf = useMemo(() => statusLookup(props.checklist, statuses.overrides), [props.checklist, statuses.overrides]);
  const allClear = useAllClearSheet(scan.id, arrangement);
  const forContractor = problems.filter((finding) => statusOf(finding.id) === "needs_pro").length;

  const fixRoom = fixRoomCard(modelLabel, { scanId: scan.id, revision: scene.revision, onOpen: openFixedLayout, onOneAtATime: () => setFixingHere(true) });

  const content: Record<Panel | Tool, () => ReactNode> = {
    waiting: () => <WaitingPanel journey={journey} />,
    failed: () => <WaitingPanel journey={journey} />,
    answers: () => <EarlyPanel {...props} />,
    counter: () => <CounterStep scanId={scan.id} scene={scene} picked={counter} onSkip={() => setCounterSkipped(true)} />,
    path: () => <PathStep path={path} />,
    plan: () => (
      <PlanPanel arrangement={arrangement} scanned={scanned} fixedNote={trial.fixedNote}
        pieceName={pieceLabel(scene, arrangement.activeId) ?? tryPiece?.label ?? null}
        builtIn={isBuiltIn(scene, arrangement.activeId)}
        review={<PlanReview review={review} scene={scene} finding={planFinding} onRelook={showProposal} onPreview={arrangement.setActiveId} />}
        fixPlan={fixPlanCard(modelLabel, { scanId: scan.id, revision: scene.revision, onOpen: showFixedLayout, plan: Object.values(arrangement.moves) })}
        statusOf={statusOf} onDecide={statuses.set} onAllClear={allClear.show}
        onReset={putEverythingBack} onDone={leavePlan} />
    ),
    wheelchair: () => <WheelchairPanel onDone={() => tools.setTool(null)} />,
    results: () => (
      <ResultsStep shop={props} groups={groups} statuses={statuses} selectedId={selected?.id ?? null} onShow={show} onPlan={planFor}
        fixingHere={fixingHere} onStartFixing={() => setFixingHere(true)} onShared={save.ask} onStartPlanning={tools.startPlanning} onStartWheelchair={tools.startWheelchair}
        fixRoom={fixRoom} section={resultsSection} onSection={setResultsSection} />
    ),
  };

  const model = (
    <>
      <OwnerModel scene={scene} glbUrl={props.glbUrl} scanGlbUrl={props.scanGlbUrl ?? null} setup={setup} lightweight={props.embedded} />
      {panel === "wheelchair" && <DrivingPad />}
      {trying && <LayoutStage arrangement={arrangement} scanned={scene} trial={trial} pointedIds={new Set([...pointedNodes(found), ...proposed.ids])} staff={setup.staff} />}
      <FoundLegend list={{ ...found, editing }} shown={foundShown} />
    </>
  );
  return (
    <Frame shopName={scan.name} embedded={props.embedded} end={shopEnd(props)} model={model} size={modelSize(panel)} step={panel}>
      {content[panel]()}
      <FoundSection list={{ ...foundList(found, trial, trying), editing }} shown={foundShown || trying} everywhere={trying} />
      <SavePrompt open={save.open} inApp={props.embedded} onClose={save.close} />
      <AllClearSheet open={allClear.open} opening={allClear.opening} forContractor={forContractor} onReport={allClear.seeReport} onStay={allClear.close} />
    </Frame>
  );
}

const NO_FINDINGS: Finding[] = [];

/** While a layout is tried, the found list reads the moved layout, and the plan has its own state. */
function useTrying(panel: Panel | Tool, arrangement: ReturnType<typeof useArrangement>, scene: SceneGraph, assessment: Assessment | null, onPick: () => void) {
  const trying = panel === "plan";
  const scanned = assessment?.findings ?? NO_FINDINGS;
  const found = useFoundObjects(trying ? arrangement.shown : scene, onPick);
  const trial = useTryLayout(arrangement, scene, scanned);
  return { trying, found, trial, scanned };
}

/** The pieces of the found row the owner is pointing at, outlined on the plan while a layout is tried. */
function pointedNodes(found: FoundObjects): Set<string> {
  const rowId = found.hoveredRowId ?? found.selectedRowId;
  const row = found.groups.flatMap((group) => group.rows).find((candidate) => candidate.id === rowId);
  return new Set(row?.nodeIds ?? []);
}

/** The staff-only floor stays put on a shop the owner can only read, and while they drive through it. */
function staffToChange(staff: StaffAdjuster, panel: Panel | Tool, readOnly: boolean): StaffAdjuster | null {
  return readOnly || panel === "wheelchair" ? null : staff;
}

/** The found pieces the model draws: only the pointed-at ones while a layout is tried, all of them on the steps that list them. */
function foundForModel(found: FoundObjects, trying: boolean, shown: boolean) {
  return trying ? pointedInModel(found) : foundInModel(found, shown);
}

/** The found list, counting moved pieces on each row while a layout is tried. */
function foundList(found: FoundObjects, trial: TryLayout, trying: boolean) {
  return trying ? { ...found, movedIds: trial.movedIds } : found;
}

/** The results and checklist, with sharing and the tools under them once the owner can use them. */
function ResultsStep({ shop, groups, statuses, selectedId, fixingHere, onStartFixing, onShow, onPlan, onShared, onStartPlanning, onStartWheelchair, fixRoom, section, onSection }: {
  shop: ShopProps;
  groups: ReturnType<typeof groupFindings>;
  statuses: ReturnType<typeof useStatuses>;
  selectedId: string | null;
  fixingHere: boolean;
  onStartFixing: () => void;
  onShow: (finding: Finding) => void;
  onPlan: (finding: Finding) => void;
  onShared: () => void;
  onStartPlanning: () => void;
  onStartWheelchair: () => void;
  /** The whole-room fix, shown first in the results. */
  fixRoom: ReactNode;
  section: ResultsSection;
  onSection: (section: ResultsSection) => void;
}) {
  const { scan, scene, journey, checklist, readOnly = false } = shop;
  const stillToCheck = useMemo(() => stillToCheckItems(groups.questions, shop.requests), [groups.questions, shop.requests]);
  const rows: Row[] = useMemo(
    () => checklistRows(groups.problems, checklist).map((row) => ({ ...row, status: statuses.overrides[row.finding.id] ?? row.status })),
    [groups.problems, checklist, statuses.overrides],
  );
  return (
    <ResultsPanel
      rows={rows}
      scene={scene}
      selectedId={selectedId}
      fixing={isFixing(checklist, fixingHere) && !readOnly}
      saving={statuses.saving}
      readOnly={readOnly}
      onStartFixing={onStartFixing}
      footer={shop.footer}
      stillToCheck={readOnly ? null : <StillToCheck scanId={scan.id} items={stillToCheck} />}
      pending={stillToCheckCount(stillToCheck)}
      fixRoom={fixRoom}
      section={section}
      onSection={onSection}
      actions={{ onShow, onStatus: statuses.set, onPlan }}
    >
      {!readOnly && <SharePanel scanId={scan.id} shopName={scan.name} onShared={onShared} />}
      {!readOnly && journey.tools_unlocked && <ToolsPanel scanId={scan.id} inApp={inApp()} onPlan={onStartPlanning} onWheelchair={onStartWheelchair} />}
    </ResultsPanel>
  );
}
