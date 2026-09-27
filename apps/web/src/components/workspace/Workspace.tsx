"use client";

import { ArrowLeft, ArrowsLeftRight, Camera, CaretDown, DotsThree, DownloadSimple, FileText, HandGrabbing, ListChecks, MapPin } from "@phosphor-icons/react";
import { DeleteScanButton } from "@/components/workspace/DeleteScanButton";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { Button, buttonClassName } from "@/components/ui/Button";
import { Menu, MENU_ITEM } from "@/components/ui/Menu";
import { overviewPose, poseAtPoint, poseFromLocus, topDownPose, type ViewerPose } from "@/lib/camera";
import { nodePosition } from "@/lib/review-targets";
import { interpolateLayout } from "@/lib/compare";
import { type FindingGroups, findingForNode, type Focus, focusOnLocus, groupFindings } from "@/lib/findings";
import { AskBox } from "./AskBox";
import { type CheckScope, scanStatus } from "@/lib/scan-status";
import { METERS_PER_INCH } from "@/lib/moves";
import { capturedMeshUrl } from "@/lib/lidar-mesh";
import type { Assessment, EvidenceStatus, Finding, Locus, NodeMove, Scan, Scenario, SceneGraph, SceneNode } from "@/types/contracts";
import { RoutePanel } from "./RoutePanel";
import type { RouteHandles } from "./StopMarkers";
import { type RouteState, useRoute } from "./useRoute";
import { ArrangePanel } from "./ArrangePanel";
import { type Comparison, ComparePanel } from "./ComparePanel";
import { RefreshWhile } from "@/components/RefreshWhile";
import { FindingsList, ProblemCount } from "./FindingsList";
import { FixSuggestion } from "./FixSuggestion";
import { LoopRun } from "./LoopRun";
import { PickedObject } from "./PickedObject";
import { WheelchairHud } from "./WheelchairHud";
import type { WheelchairState } from "./WheelchairController";
import type { ArrangeHandlers } from "./ShopModel";
import { type Arrangement, useArrangement } from "./useArrangement";
import { type Combine, useCombine } from "./useCombine";
import { CombinePanel } from "./CombinePanel";
import type { RoomGroup } from "@/lib/room-groups";
import { SimulationPanel } from "./SimulationPanel";
import { type MaterialMode, type ViewMode, ViewerDock } from "./ViewerDock";
import { isTextureRefreshing, textureStatusMatches } from "@/lib/texture-status";
import { showsSplats, viewerSourcePlan } from "@/lib/viewer-source";

import type { TextureStatus } from "@/types/contracts";

import type { CapturedSplats } from "@/lib/captured-splats";
import { DEFAULT_WHEELCHAIR_PROFILE, wheelchairProfile, type MotionPoint, type WheelchairProfile } from "@/lib/wheelchair-motion";
import { ReviewPanel } from "./ReviewPanel";
import { EvidencePanel } from "./EvidencePanel";
import { OutcomeMatrix } from "./OutcomeMatrix";
import type { LaneView } from "@/lib/evidence";
import { useDeveloperMode } from "@/lib/developer-mode";




const Viewer = dynamic(() => import("./Viewer"), {
  ssr: false,
  loading: () => <div className="h-full w-full animate-pulse bg-rule/30" />,
});

type Previous = { scene: SceneGraph; assessment: Assessment | null } | null;

type WorkspaceProps = {
  scan: Scan;
  scene: SceneGraph;
  exported: SceneGraph;
  assessment: Assessment | null;
  previous: Previous;
  glbUrl: string | null;
  lidarUrl: string | null;
  scenario: Scenario | null;
  suggestedScenario: Scenario | null;
  textureStatus: TextureStatus | null;
  rooms: RoomGroup[];
  capturedSplats?: CapturedSplats | null;
  evidence?: EvidenceStatus | null;
  team: boolean;
};

function isWorking(scan: Scan): boolean {
  return scan.state === "uploading" || scan.state === "measuring" || scan.state === "checking";
}
type Task = "findings" | "arrange" | "combine" | "compare" | "route" | "review";

/** The nodes of the walk being placed, so the viewer can pick it out of the four. */
function activeRoomNodeIds(combine: Combine): string[] | null {
  if (!combine.activeRoom) return null;
  return combine.rooms.find((room) => room.name === combine.activeRoom)?.node_ids ?? null;
}

function poseFor(scene: SceneGraph, selected: Focus | null, mode: ViewMode, focusPoint?: { x: number; y: number; z: number } | null): ViewerPose {
  if (selected?.locus) return poseFromLocus(selected.locus.camera);
  if (focusPoint) return poseAtPoint(focusPoint, scene);
  return mode === "top" ? topDownPose(scene) : overviewPose(scene);
}

function comparisonFor(arrangement: Arrangement, scene: SceneGraph, findings: Finding[], previous: Previous): Comparison | null {
  if (arrangement.hasMoves && arrangement.check) {
    return {
      before: scene, after: arrangement.shown, beforeFindings: findings, afterFindings: arrangement.check.findings,
      beforeLabel: "Now", afterLabel: "With your moves",
    };
  }
  if (!previous || previous.assessment === null) return null;
  return {
    before: previous.scene, after: scene, beforeFindings: previous.assessment?.findings ?? [], afterFindings: findings,
    beforeLabel: "Before", afterLabel: "After",
  };
}

const NUDGES: Record<string, [number, number]> = {
  ArrowUp: [0, 1],
  ArrowDown: [0, -1],
  ArrowLeft: [-1, 0],
  ArrowRight: [1, 0],
};

function nudgeFor(event: KeyboardEvent, nudge: (dx: number, dy: number, degrees: number) => void) {
  const inches = (event.shiftKey ? 6 : 1) * METERS_PER_INCH;
  const move = NUDGES[event.key];
  if (move) {
    event.preventDefault();
    nudge(move[0] * inches, move[1] * inches, 0);
  } else if (event.key === "r" || event.key === "R") {
    nudge(0, 0, event.shiftKey ? -15 : 15);
  }
}

function useKeyboard(task: Task, arrangement: Arrangement, combine: Combine, clear: () => void, wheelchairMode: boolean) {
  useEffect(() => {
    if (wheelchairMode) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        clear();
        arrangement.setActiveId(null);
        combine.setActiveRoom(null);
        return;
      }
      const typing = event.target instanceof HTMLElement && event.target.closest("input, textarea, select, nav, [contenteditable=true]");
      if (typing) return;
      if (task === "arrange" && arrangement.activeId) nudgeFor(event, arrangement.nudge);
      if (task === "combine" && combine.activeRoom) nudgeFor(event, combine.nudge);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [task, arrangement, combine, clear, wheelchairMode]);
}

function useArrangeHandlers(enabled: boolean, arrangement: Arrangement, setDragging: (on: boolean) => void) {
  const { setActiveId, drag, drop, activeId, blockedIds } = arrangement;
  return useMemo<ArrangeHandlers | null>(
    () =>
      !enabled
        ? null
        : {
            activeId,
            blockedIds,
            onGrab: (nodeId) => {
              setActiveId(nodeId);
              setDragging(true);
            },
            onDrag: drag,
            onDrop: () => {
              setDragging(false);
              drop();
            },
          },
    [enabled, activeId, blockedIds, setActiveId, drag, drop, setDragging],
  );
}

const EMPTY_SET = new Set<string>();

function useCombineHandlers(enabled: boolean, combine: Combine, setDragging: (on: boolean) => void) {
  const { activeRoom, onGrab, onDrag } = combine;
  return useMemo<ArrangeHandlers | null>(
    () =>
      !enabled
        ? null
        : {
            activeId: activeRoom,
            blockedIds: EMPTY_SET,
            onGrab: (nodeId) => {
              onGrab(nodeId);
              setDragging(true);
            },
            onDrag,
            onDrop: () => setDragging(false),
          },
    [enabled, activeRoom, onGrab, onDrag, setDragging],
  );
}

type HeaderProps = { scan: Scan; revision: number; task: Task; canCompare: boolean; canCombine: boolean; onTask: (task: Task) => void };

const TASKS: { task: Task; label: string; icon: typeof ListChecks }[] = [
  { task: "findings", label: "Problems", icon: ListChecks },
  { task: "arrange", label: "Move things", icon: HandGrabbing },
  { task: "combine", label: "Combine", icon: HandGrabbing },
  { task: "route", label: "Customer path", icon: MapPin },
  { task: "review", label: "Check photos", icon: Camera },
  { task: "compare", label: "Before and after", icon: ArrowsLeftRight },
];

function TaskSwitch({ task, available, onTask }: { task: Task; available: (task: Task) => boolean; onTask: (task: Task) => void }) {
  return (
    <div className="flex max-w-full gap-1 overflow-x-auto rounded-xl bg-rule/50 p-1" role="group" aria-label="What to do">
      {TASKS.filter((entry) => available(entry.task)).map(({ task: entry, label, icon: Icon }) => (
        <Button key={entry} variant="chip" aria-pressed={task === entry} onClick={() => onTask(entry)}>
          <Icon size={16} weight="bold" aria-hidden />
          {label}
        </Button>
      ))}
    </div>
  );
}

function ShopMenu({ scan, revision }: { scan: Scan; revision: number }) {
  return (
    <Menu label="More" icon={<DotsThree size={18} weight="bold" aria-hidden />}>
      <Link href={`/scans/${scan.id}/report`} className={MENU_ITEM}>
        <FileText size={18} aria-hidden />
        Open the report
      </Link>
      <a href={`/api/scans/${scan.id}/architecture.zip?revision=${revision}`} download className={MENU_ITEM}>
        <DownloadSimple size={18} aria-hidden />
        Download the floor plan
      </a>
      <DeleteScanButton scanId={scan.id} name={scan.name} />
    </Menu>
  );
}

function WorkspaceHeader({ scan, revision, task, canCompare, canCombine, onTask }: HeaderProps) {
  const available = (entry: Task) => (entry !== "compare" || canCompare) && (entry !== "combine" || canCombine);
  return (
    <header className="flex flex-wrap items-center gap-3 px-3 py-3 lg:col-span-2">
      <Link href="/" className="rounded-lg p-2 text-ink-muted hover:bg-ink/5 hover:text-ink" aria-label="Your shops">
        <ArrowLeft size={20} weight="bold" aria-hidden />
      </Link>
      <h1 className="heading-display min-w-0 flex-1 truncate text-lg" title={scan.name}>{scan.name}</h1>
      <ShopMenu scan={scan} revision={revision} />
      <TaskSwitch task={task} available={available} onTask={onTask} />
    </header>
  );
}

type SidePanelProps = {
  task: Task;
  scene: SceneGraph;
  onTryLayout: (moves: NodeMove[]) => void;
  onPreviewLayout: (moves: NodeMove[]) => void;
  assessment: Assessment | null;
  scan: Scan;
  findings: Finding[];
  selected: Finding | null;
  arrangement: Arrangement;
  combine: Combine;
  comparison: Comparison | null;
  amount: number;
  onAmount: (value: number) => void;
  onToggle: (finding: Finding) => void;
  route: RouteState;
  onRoute: () => void;
  onLook: (locus: Locus | null) => void;
  selectedNodeId?: string | null;
  onSelectNode?: (nodeId: string | null) => void;
  onReviewPersisted?: (scene: SceneGraph) => void;
  developer: boolean;
};

function findingsTaskPanel(props: SidePanelProps): ReactNode {
  const { scene, scan, findings, selected, onTryLayout, onPreviewLayout, onToggle, onLook, route, onRoute, assessment, developer } = props;
  return (
    <div className="flex flex-col gap-6">
      <FindingsPanel scan={scan} scene={scene} assessment={assessment} findings={findings} selected={selected} onToggle={onToggle} onTryLayout={onTryLayout} route={route} onRoute={onRoute} developer={developer} />
      {developer && (
        <MoreTools>
          <AskBox scanId={scan.id} revision={scene.revision} onLook={onLook} onTry={onTryLayout} />
          <SimulationPanel key={`${scan.id}-${scene.revision}`} scanId={scan.id} scene={scene} onTryLayout={onTryLayout} onPreviewLayout={onPreviewLayout} />
          {assessment?.scope && <OutcomeMatrix scope={assessment.scope} findings={findings} />}
        </MoreTools>
      )}
    </div>
  );
}

/** The team's own tools, in developer mode only and one tap away even then. */
function MoreTools({ children }: { children: ReactNode }) {
  return (
    <details className="group border-t border-rule/60 pt-3">
      <summary className="flex cursor-pointer list-none items-center gap-2 rounded-lg px-3 py-2 font-medium text-ink-muted hover:bg-ink/[0.04] [&::-webkit-details-marker]:hidden">
        <CaretDown size={16} weight="bold" className="-rotate-90 transition-transform group-open:rotate-0" aria-hidden />
        More tools
      </summary>
      <div className="mt-3 flex flex-col gap-6">{children}</div>
    </details>
  );
}

function reviewTaskPanel(props: SidePanelProps): ReactNode {
  const { scan, scene, selectedNodeId, onSelectNode, onReviewPersisted } = props;
  return (
    <ReviewPanel
      scanId={scan.id}
      scene={scene}
      selectedId={selectedNodeId ?? null}
      onSelectNode={onSelectNode ?? (() => {})}
      onPersisted={onReviewPersisted}
    />
  );
}

function compareTaskPanel(props: SidePanelProps): ReactNode {
  const { comparison, amount, onAmount, assessment, route } = props;
  const scope: CheckScope = { rulesChecked: assessment?.rules_checked ?? null, routeConfirmed: route.confirmed };
  return comparison ? <ComparePanel comparison={comparison} amount={amount} onAmount={onAmount} scope={scope} /> : null;
}

function arrangeTaskPanel(props: SidePanelProps): ReactNode {
  const { findings, arrangement, assessment, route } = props;
  const scope: CheckScope = { rulesChecked: assessment?.rules_checked ?? null, routeConfirmed: route.confirmed };
  return <ArrangePanel arrangement={arrangement} fallbackFindings={findings} scope={scope} />;
}

function combineTaskPanel(props: SidePanelProps): ReactNode {
  return <CombinePanel combine={props.combine} />;
}

function routeTaskPanel(props: SidePanelProps): ReactNode {
  return <RoutePanel route={props.route} />;
}

const TASK_PANELS: Record<Task, (props: SidePanelProps) => ReactNode> = {
  review: reviewTaskPanel,
  compare: compareTaskPanel,
  arrange: arrangeTaskPanel,
  combine: combineTaskPanel,
  route: routeTaskPanel,
  findings: findingsTaskPanel,
};

function SidePanel(props: SidePanelProps) {
  return TASK_PANELS[props.task](props);
}

type FindingsPanelProps = Pick<SidePanelProps, "scan" | "scene" | "assessment" | "findings" | "selected" | "onToggle" | "onTryLayout" | "route" | "onRoute" | "developer">;

function RoutePrompt({ onRoute }: { onRoute: () => void }) {
  return (
    <Button variant="primary" onClick={onRoute} className="mx-3 self-start">
      <MapPin size={18} weight="bold" aria-hidden />
      Show where customers walk
    </Button>
  );
}

/** Above the findings: confirm the route first. The improvement loop is the team's, so only they see it after. */
function NextStep({ scan, scene, route, onRoute, onTryLayout, developer }: Omit<FindingsPanelProps, "assessment" | "selected" | "onToggle" | "findings">) {
  if (!route.confirmed) return scan.state === "ready" ? <RoutePrompt onRoute={onRoute} /> : null;
  return developer ? <LoopRun key={scene.revision} scanId={scan.id} revision={scene.revision} onTry={onTryLayout} /> : null;
}

function FindingsPanel({ scan, scene, assessment, findings, selected, onToggle, onTryLayout, route, onRoute, developer }: FindingsPanelProps) {
  if (assessment === null && isWorking(scan)) {
    return <p className="px-3 font-medium" role="status">Checking this layout</p>;
  }
  const groups = groupFindings(findings);
  return (
    <div className="flex flex-col gap-5">
      {findings.length > 0 && <ProblemCount groups={groups} routeConfirmed={route.confirmed} />}
      <NextStep scan={scan} scene={scene} route={route} onRoute={onRoute} onTryLayout={onTryLayout} developer={developer} />
      {findings.length === 0
        ? <p className="px-3 text-ink-muted">{scanStatus(scan, assessment, route.confirmed)}</p>
        : <GroupedFindings scan={scan} scene={scene} groups={groups} selected={selected} onToggle={onToggle} onTryLayout={onTryLayout} />}
      {assessment !== null && <ReportLink scanId={scan.id} />}
    </div>
  );
}

function ReportLink({ scanId }: { scanId: string }) {
  return (
    <Link href={`/scans/${scanId}/report`} className={`${buttonClassName("quiet")} self-start`}>
      <FileText size={18} weight="bold" aria-hidden />
      Open the report
    </Link>
  );
}

function GroupedFindings({ scan, scene, groups, selected, onToggle, onTryLayout }: Pick<FindingsPanelProps, "scan" | "scene" | "selected" | "onToggle" | "onTryLayout"> & { groups: FindingGroups }) {
  return (
    <FindingsList
      groups={groups}
      selectedId={selected?.id ?? null}
      onSelect={onToggle}
      extra={(finding) => <FixSuggestion scanId={scan.id} scene={scene} finding={finding} onTry={onTryLayout} />}
    />
  );
}

type Picked = { id: string; label: string };

/** The piece last tapped in the model, read from the current scene so a relabel shows at once. */
function usePicked(scene: SceneGraph) {
  const [picked, setPicked] = useState<Picked | null>(null);
  const node = picked ? scene.nodes.find((candidate) => candidate.id === picked.id) ?? null : null;
  return { setPicked, node, label: node?.label ?? picked?.label ?? null };
}

const canMarkCounter = (task: Task, scan: Scan) => task === "findings" && scan.state === "ready";

function useRouteHandles(task: Task, route: RouteState, setDragging: (on: boolean) => void): RouteHandles | null {
  return useMemo(
    () =>
      task !== "route"
        ? null
        : { markers: route.markers, editable: true, onGrab: () => setDragging(true), onDrag: route.drag, onDrop: () => setDragging(false) },
    [task, route.markers, route.drag, setDragging],
  );
}

/** The finding picked from the list, or else the subject of the last question. */
function useFocus() {
  const [selected, setSelected] = useState<Finding | null>(null);
  const [asked, setAsked] = useState<Focus | null>(null);
  const focus: Focus | null = selected ?? asked;
  return { selected, setSelected, setAsked, focus };
}

function useWorkspaceVisuals(props: WorkspaceProps, findings: Finding[], task: Task, amount: number, focus: Focus | null, focusPoint: { x: number; y: number; z: number } | null, mode: ViewMode, setFocusPoint: (point: { x: number; y: number; z: number } | null) => void, setDragging: (on: boolean) => void) {
  const arrangement = useArrangement(props.scan.id, props.scene);
  const combine = useCombine(props.scan.id, props.scene, props.rooms);
  const comparison = comparisonFor(arrangement, props.scene, findings, props.previous);
  const shown = task === "combine" ? combine.shown : (task === "compare" && comparison ? interpolateLayout(comparison.before, comparison.after, amount) : arrangement.shown);
  const pose = useMemo(() => poseFor(props.scene, focus, mode, focusPoint), [props.scene, focus, mode, focusPoint]);
  const arrangeHandlers = useArrangeHandlers(task === "arrange", arrangement, setDragging);
  const combineHandlers = useCombineHandlers(task === "combine", combine, setDragging);
  const handlers = task === "combine" ? combineHandlers : arrangeHandlers;
  const route = useRoute(props.scan.id, props.scenario, props.suggestedScenario);
  return { arrangement, combine, comparison, shown, pose, handlers, dragAllNodes: task === "combine", route, routeHandles: useRouteHandles(task, route, setDragging), setFocusPoint };
}

function useWorkspaceActions(findings: Finding[], scene: SceneGraph, arrangement: Arrangement, setSelected: (finding: Finding | null | ((current: Finding | null) => Finding | null)) => void, setAsked: (focus: Focus | null) => void, setPicked: (picked: Picked | null) => void, setTask: (task: Task) => void, setMode: (mode: ViewMode) => void, setAmount: (amount: number) => void) {
  const preview = arrangement.preview;
  const clear = useCallback(() => { setSelected(null); setAsked(null); setPicked(null); }, [setSelected, setAsked, setPicked]);
  const selectNode = useCallback((nodeId: string) => {
    setSelected(findingForNode(findings, nodeId) ?? null);
    setPicked({ id: nodeId, label: scene.nodes.find((node) => node.id === nodeId)?.label ?? "Scanned surface" });
  }, [findings, scene, setSelected, setPicked]);
  const toggle = (finding: Finding) => setSelected((current) => current?.id === finding.id ? null : finding);
  const showView = (next: ViewMode) => { setSelected(null); setMode(next); };
  const switchTask = (next: Task) => {
    clear();
    if (next === "findings") arrangement.reset();
    if (next === "compare") setAmount(0);
    setTask(next);
  };
  const tryLayout = (moves: NodeMove[]) => {
    setSelected(null);
    setTask("arrange");
    arrangement.load(moves);
    requestAnimationFrame(() => document.querySelector<HTMLButtonElement>('[aria-label="What to do"] [aria-pressed="true"]')?.focus());
  };
  const previewLayout = useCallback((moves: NodeMove[]) => {
    setSelected(null);
    preview(moves);
  }, [preview, setSelected]);
  const look = (locus: Locus | null) => { setSelected(null); setAsked(locus ? focusOnLocus(locus) : null); };
  return { clear, selectNode, toggle, showView, switchTask, tryLayout, previewLayout, look };
}

function usePhotoTextures(scanId: string, revision: number, initial: TextureStatus | null) {
  const key = `${scanId}:${revision}`;
  const currentKey = useRef(key);
  useLayoutEffect(() => { currentKey.current = key; }, [key]);
  const [snapshot, setSnapshot] = useState(() => ({ key, status: initial }));
  const [requestingKey, setRequestingKey] = useState<string | null>(null);
  const [requestError, setRequestError] = useState<{ key: string; message: string } | null>(null);
  const status = snapshot.key === key ? snapshot.status : initial;
  const requesting = requestingKey === key;
  const error = requestError?.key === key ? requestError.message : null;

  const save = useCallback((response: TextureStatus) => {
    if (currentKey.current !== key || !textureStatusMatches(response, scanId, revision)) return;
    setSnapshot({ key, status: response });
  }, [key, revision, scanId]);

  const refresh = useCallback(async () => {
    try {
      const response = await fetch(`/api/scans/${scanId}/textures?revision=${revision}`, { cache: "no-store" });
      if (!response.ok) return;
      save(await response.json() as TextureStatus);
    } catch { /* The clean reconstructed model stays useful while the network reconnects. */ }
  }, [save, scanId, revision]);

  useEffect(() => { void refresh(); }, [refresh]);

  useEffect(() => {
    if (!status || !isTextureRefreshing(status.state)) return;
    const timer = window.setInterval(() => { void refresh(); }, 2000);
    return () => window.clearInterval(timer);
  }, [status, refresh]);

  useEffect(() => {
    if (!error) return;
    const timer = window.setTimeout(() => setRequestError((current) => current?.key === key ? null : current), 4000);
    return () => window.clearTimeout(timer);
  }, [error, key]);

  const request = useCallback(async () => {
    setRequestingKey(key);
    setRequestError(null);
    try {
      const response = await fetch(`/api/scans/${scanId}/textures`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ revision }),
      });
      if (!response.ok) throw new Error("Texture build request failed");
      save(await response.json() as TextureStatus);
    } catch {
      if (currentKey.current === key) setRequestError({ key, message: "Couldn’t start textures. Try again." });
    } finally {
      if (currentKey.current === key) setRequestingKey(null);
    }
  }, [key, revision, save, scanId]);
  return { status, requesting, request, error };
}

type WorkspaceBodyProps = WorkspaceProps & {
  findings: Finding[]; task: Task; selected: Finding | null; focus: Focus | null; mode: ViewMode; picked: ReturnType<typeof usePicked>; dragging: boolean; amount: number; setAmount: (amount: number) => void; showScanEvidence: boolean; setShowScanEvidence: (value: boolean | ((current: boolean) => boolean)) => void; visuals: ReturnType<typeof useWorkspaceVisuals>; actions: ReturnType<typeof useWorkspaceActions>;
};

// The workspace deliberately coordinates several independent panels around one model.
// eslint-disable-next-line complexity
function WorkspaceBody({ scan, scene, exported, assessment, glbUrl, lidarUrl, textureStatus, capturedSplats, evidence, team, findings, task, selected, focus, mode, picked, dragging, amount, setAmount, showScanEvidence, setShowScanEvidence, visuals, actions }: WorkspaceBodyProps) {
  const router = useRouter();
  const [cutWalls, setCutWalls] = useState(true);
  const [developerChosen] = useDeveloperMode();
  const developer = team && developerChosen;
  const [chosenMaterialMode, setChosenMaterialMode] = useState<MaterialMode | null>(null);
  const [wheelchairMode, setWheelchairMode] = useState(false);
  const [wheelchairState, setWheelchairState] = useState<WheelchairState | null>(null);
  const [profile, setProfile] = useState<WheelchairProfile>(DEFAULT_WHEELCHAIR_PROFILE);
  const [dockTarget, setDockTarget] = useState<SceneNode | null>(null);
  const [dockDestination, setDockDestination] = useState<MotionPoint | null>(null);
  const [splatError, setSplatError] = useState<string | null>(null);
  useKeyboard(task, visuals.arrangement, visuals.combine, actions.clear, wheelchairMode);

  const toggleWheelchairMode = useCallback(() => {
    setWheelchairMode((curr) => !curr);
  }, []);

  const exitWheelchairMode = useCallback(() => {
    setWheelchairMode(false);
    setDockTarget(null);
    setDockDestination(null);
  }, []);

  const handleDockWheelchair = useCallback((node: SceneNode) => {
    setDockTarget(node);
    setDockDestination(null);
  }, []);

  const handleClearWheelchairDock = useCallback(() => {
    setDockTarget(null);
    setDockDestination(null);
  }, []);

  const handleWheelchairSelectNode = useCallback((node: SceneNode) => {
    actions.selectNode(node.id);
  }, [actions]);

  const handleWheelchairProfile = useCallback((next: WheelchairProfile) => {
    setProfile(wheelchairProfile(next));
  }, []);

  const textures = usePhotoTextures(scan.id, scene.revision, textureStatus);
  const evidenceAvailable = lidarUrl !== null && scene.revision === 0 && task !== "arrange" && task !== "compare";
  const displayedLidarUrl = capturedMeshUrl(lidarUrl, scene.revision, showScanEvidence && evidenceAvailable);
  const activeMode = selected ? null : mode;
  const photoBuild = textures.status?.build ?? null;
  const scanGlbUrl = photoBuild?.scan_glb_url ?? null;
  const captureAllowed = task === "findings" || task === "route" || task === "review";
  const splatAssets = captureAllowed && capturedSplats?.revision === scene.revision ? capturedSplats.assets : undefined;
  const hasSplats = Boolean(splatAssets?.length);
  const reconstructionCount = scene.nodes.filter((node) => node.reconstruction !== null && node.reconstruction !== undefined).length;
  const preferredMode = chosenMaterialMode ?? (scanGlbUrl ? "scan" : reconstructionCount > 0 ? "reconstructed" : (hasSplats ? "splat" : "captured"));
  const materialMode = !captureAllowed && (preferredMode === "scan" || preferredMode === "splat") ? "plain" : preferredMode;
  const splatsOnScreen = showsSplats({ materialMode, hasSplats, hasScanGlb: scanGlbUrl !== null });
  const reconstructionPending = reconstructionCount > 0 && exported.revision !== scene.revision;
  const cleanGlbUrl = reconstructionPending ? null : glbUrl;
  const sourcePlan = viewerSourcePlan({
    materialMode,
    hasCleanGlb: cleanGlbUrl !== null,
    hasPhotoBuild: photoBuild !== null,
    staleNodeIds: textures.status?.stale_node_ids ?? [],
  });
  const sourceGlbUrl = sourcePlan.usePhotoBuild ? photoBuild?.glb_url ?? null : cleanGlbUrl;
  const sourceGraph = sourcePlan.usePhotoBuild ? photoBuild?.bake_graph ?? exported : exported;

  const [persistedScene, setPersistedScene] = useState<SceneGraph | null>(null);

  const handleReviewPersisted = useCallback((updated: SceneGraph) => {
    setPersistedScene(updated);
  }, []);

  const activeScene = useMemo(() => persistedScene ?? scene, [scene, persistedScene]);

  const handleEvidenceAction = useCallback((lane: LaneView) => {
    if (lane.id === "recognition" && lane.tone === "failed") {
      void fetch(`/api/scans/${scan.id}/complete`, { method: "POST" }).then((response) => {
        if (response.ok) router.refresh();
      });
    }
    if (lane.id === "visual") void textures.request();
  }, [scan.id, textures, router]);

  return <>
    <RefreshWhile pending={assessment === null && isWorking(scan)} />
    <div className={`grid h-dvh grid-cols-[minmax(0,1fr)] ${wheelchairMode ? "grid-rows-[auto_minmax(0,1fr)]" : "grid-rows-[auto_minmax(16rem,45dvh)_1fr] lg:grid-cols-[minmax(0,1fr)_24rem] lg:grid-rows-[auto_1fr]"}`}>
      <WorkspaceHeader scan={scan} revision={scene.revision} task={task} canCompare={developer && visuals.comparison !== null} canCombine={developer && visuals.combine.rooms.length > 1} onTask={actions.switchTask} />
      <section className="relative min-h-0 touch-none overflow-hidden lg:rounded-tr-2xl" aria-label="Shop model">
        <Viewer
          scene={visuals.shown}
          highlightNodeIds={task === "combine" ? activeRoomNodeIds(visuals.combine) : null}
          combinedRooms={task === "combine" ? { rooms: visuals.combine.rooms, placements: visuals.combine.placements } : null}
          exported={sourceGraph}
          arrange={wheelchairMode ? null : visuals.handlers}
          dragAllNodes={visuals.dragAllNodes}
          lightweight={task === "combine"}
          route={visuals.routeHandles}
          dragging={dragging}
          cutWalls={wheelchairMode ? false : cutWalls}
          glbUrl={sourceGlbUrl}
          scanGlbUrl={scanGlbUrl}
          splatAssets={splatAssets}
          onSplatError={setSplatError}
          lidarUrl={displayedLidarUrl}
          pose={visuals.pose}
          selected={task === "findings" ? focus : null}
          onSelectNode={actions.selectNode}
          onClearSelection={actions.clear}
          materialMode={sourcePlan.materialMode}
          staleNodeIds={sourcePlan.staleNodeIds}
          coverage={photoBuild?.coverage.nodes ?? []}
          wheelchairMode={wheelchairMode}
          wheelchairProfile={profile}
          onWheelchairStateChange={setWheelchairState}
          wheelchairDockTarget={dockTarget}
          wheelchairDockDestination={dockDestination}
          onClearWheelchairDock={handleClearWheelchairDock}
          onWheelchairSelectNode={handleWheelchairSelectNode}
          onWheelchairExit={exitWheelchairMode}
        />

        {splatError && splatsOnScreen && <p role="status" className="absolute left-4 top-4 max-w-sm rounded-lg bg-sheet/95 p-3 text-sm text-ink-muted">{splatError} Showing the measured model.</p>}
        {splatsOnScreen && !splatError && !wheelchairMode && (
          <p role="status" className="pointer-events-none absolute left-4 top-4 max-w-sm rounded-lg bg-sheet/90 px-3 py-2 text-xs text-ink-muted shadow-sm">
            Gaps and blur remain in this view. Measurements use the scan geometry.
          </p>
        )}
        <PickedObject
          scanId={scan.id}
          revision={scene.revision}
          label={wheelchairMode ? null : picked.label}
          node={wheelchairMode ? null : picked.node}
          editable={canMarkCounter(task, scan)}
        />
        <WheelchairHud
          active={wheelchairMode}
          state={wheelchairState}
          onExit={exitWheelchairMode}
          onDock={handleDockWheelchair}
          onSelectNode={actions.selectNode}
          profile={profile}
          onProfileChange={handleWheelchairProfile}
        />
        <ViewerDock
          activeMode={activeMode}
          onView={actions.showView}
          wheelchairMode={wheelchairMode}
          onToggleWheelchair={toggleWheelchairMode}
          visibility={{ cutWalls, onToggleWalls: () => setCutWalls((current) => !current), evidenceAvailable, evidenceShown: showScanEvidence, onToggleEvidence: () => setShowScanEvidence((current) => !current) }}
          textures={{ status: textures.status, requesting: textures.requesting, error: textures.error, mode: materialMode === "splat" ? "plain" : materialMode, onMode: setChosenMaterialMode, onRequest: () => { void textures.request(); }, reconstruction: { count: reconstructionCount, pending: reconstructionPending }, capturedSplats: hasSplats }}
          downloadUrl={sourceGlbUrl && !visuals.arrangement.hasMoves && task !== "compare" ? sourceGlbUrl : null}
        />
      </section>
      <aside hidden={wheelchairMode} className="min-h-0 overflow-y-auto px-3 pb-10 pt-4 lg:pt-0">
        <div className="mb-3 flex flex-col gap-3">
          {developer && !wheelchairMode && (
            <EvidencePanel
              status={evidence ?? null}
              textures={textures.status}
              measured={scene !== null}
              onAction={handleEvidenceAction}
            />
          )}
        </div>
        <SidePanel
          developer={developer}
          task={task}
          scene={activeScene}
          onTryLayout={actions.tryLayout}
          onPreviewLayout={actions.previewLayout}
          assessment={assessment}
          scan={scan}
          findings={findings}
          selected={selected}
          arrangement={visuals.arrangement}
          combine={visuals.combine}
          comparison={visuals.comparison}
          amount={amount}
          onAmount={setAmount}
          onToggle={actions.toggle}
          route={visuals.route}
          onRoute={() => actions.switchTask("route")}
          onLook={actions.look}
          selectedNodeId={picked.node?.id ?? null}
          onSelectNode={(id) => {
            if (id) {
              const node = activeScene.nodes.find((candidate) => candidate.id === id);
              actions.selectNode(id);
              if (task === "review" && node) visuals.setFocusPoint(nodePosition(node));
              return;
            }
            actions.clear();
            visuals.setFocusPoint(null);
          }}
          onReviewPersisted={handleReviewPersisted}
        />

      </aside>
    </div>
  </>;
}

export function Workspace(props: WorkspaceProps) {
  const findings = useMemo(() => props.assessment?.findings ?? [], [props.assessment]);
  const { selected, setSelected, setAsked, focus } = useFocus();
  const [mode, setMode] = useState<ViewMode>("overview");
  const [focusPoint, setFocusPoint] = useState<{ x: number; y: number; z: number } | null>(null);
  const picked = usePicked(props.scene);
  const [task, setTask] = useState<Task>("findings");
  const [dragging, setDragging] = useState(false);
  const [amount, setAmount] = useState(1);
  const [showScanEvidence, setShowScanEvidence] = useState(false);
  const visuals = useWorkspaceVisuals(props, findings, task, amount, focus, focusPoint, mode, setFocusPoint, setDragging);
  const actions = useWorkspaceActions(findings, props.scene, visuals.arrangement, setSelected, setAsked, picked.setPicked, setTask, setMode, setAmount);
  return <WorkspaceBody {...props} findings={findings} task={task} selected={selected} focus={focus} mode={mode} picked={picked} dragging={dragging} amount={amount} setAmount={setAmount} showScanEvidence={showScanEvidence} setShowScanEvidence={setShowScanEvidence} visuals={visuals} actions={actions} />;
}
