"use client";

import { ArrowLeft, ArrowsLeftRight, ArrowsOutCardinal, Eye, FileText, HandGrabbing, ListChecks, MapPin, SquareHalfBottom } from "@phosphor-icons/react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/Button";
import { overviewPose, poseFromLocus, topDownPose, type ViewerPose } from "@/lib/camera";
import { interpolateLayout } from "@/lib/compare";
import { findingForNode, type Focus, focusOnLocus, groupFindings } from "@/lib/findings";
import { AskBox } from "./AskBox";
import { type CheckScope, scanStatus } from "@/lib/scan-status";
import { METERS_PER_INCH } from "@/lib/moves";
import { capturedMeshUrl } from "@/lib/lidar-mesh";
import type { Assessment, Finding, Locus, NodeMove, Scan, Scenario, SceneGraph } from "@/types/contracts";
import { RoutePanel } from "./RoutePanel";
import type { RouteHandles } from "./StopMarkers";
import { type RouteState, useRoute } from "./useRoute";
import { ArrangePanel } from "./ArrangePanel";
import { type Comparison, ComparePanel } from "./ComparePanel";
import { RefreshWhile } from "@/components/RefreshWhile";
import { FindingsList } from "./FindingsList";
import { FixSuggestion } from "./FixSuggestion";
import { LoopRun } from "./LoopRun";
import { PickedObject } from "./PickedObject";
import type { ArrangeHandlers } from "./ShopModel";
import { type Arrangement, useArrangement } from "./useArrangement";
import { SimulationPanel } from "./SimulationPanel";

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
};

type ViewMode = "overview" | "top";

function isWorking(scan: Scan): boolean {
  return scan.state === "uploading" || scan.state === "measuring" || scan.state === "checking";
}
type Task = "findings" | "arrange" | "compare" | "route";

function poseFor(scene: SceneGraph, selected: Focus | null, mode: ViewMode): ViewerPose {
  if (selected?.locus) return poseFromLocus(selected.locus.camera);
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

function arrangeKey(event: KeyboardEvent, arrangement: Arrangement) {
  const inches = (event.shiftKey ? 6 : 1) * METERS_PER_INCH;
  const nudge = NUDGES[event.key];
  if (nudge) {
    event.preventDefault();
    arrangement.nudge(nudge[0] * inches, nudge[1] * inches, 0);
  } else if (event.key === "r" || event.key === "R") {
    arrangement.nudge(0, 0, event.shiftKey ? -15 : 15);
  }
}

function useKeyboard(task: Task, arrangement: Arrangement, clear: () => void) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        clear();
        arrangement.setActiveId(null);
        return;
      }
      const typing = (event.target as HTMLElement).closest("input, textarea, nav");
      if (task === "arrange" && arrangement.activeId && !typing) arrangeKey(event, arrangement);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [task, arrangement, clear]);
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

type HeaderProps = { scan: Scan; task: Task; canCompare: boolean; onTask: (task: Task) => void };

function WorkspaceHeader({ scan, task, canCompare, onTask }: HeaderProps) {
  return (
    <header className="flex flex-wrap items-center gap-3 px-3 py-3 lg:col-span-2">
      <Link href="/" className="rounded-lg p-2 text-ink-muted hover:bg-ink/5 hover:text-ink" aria-label="Your shops">
        <ArrowLeft size={20} weight="bold" />
      </Link>
      <h1 className="min-w-0 flex-1 truncate text-lg font-semibold">{scan.name}</h1>
      <Link
        href={`/scans/${scan.id}/report`}
        className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium text-ink-muted hover:bg-ink/5 hover:text-ink"
      >
        <FileText size={16} weight="bold" aria-hidden />
        Report
      </Link>
      <div className="flex max-w-full gap-1 overflow-x-auto rounded-xl bg-rule/50 p-1" role="group" aria-label="What to do">
        <Button variant="chip" aria-pressed={task === "findings"} onClick={() => onTask("findings")}>
          <ListChecks size={16} weight="bold" className="hidden sm:block" aria-hidden />
          Findings
        </Button>
        <Button variant="chip" aria-pressed={task === "arrange"} onClick={() => onTask("arrange")}>
          <HandGrabbing size={16} weight="bold" className="hidden sm:block" aria-hidden />
          Move furniture
        </Button>
        <Button variant="chip" aria-pressed={task === "route"} onClick={() => onTask("route")}>
          <MapPin size={16} weight="bold" className="hidden sm:block" aria-hidden />
          Customer route
        </Button>
        {canCompare && (
          <Button variant="chip" aria-pressed={task === "compare"} onClick={() => onTask("compare")}>
            <ArrowsLeftRight size={16} weight="bold" className="hidden sm:block" aria-hidden />
            Before and after
          </Button>
        )}
      </div>
    </header>
  );
}

type SidePanelProps = {
  task: Task;
  scene: SceneGraph;
  onTryLayout: (moves: NodeMove[]) => void;
  assessment: Assessment | null;
  scan: Scan;
  findings: Finding[];
  selected: Finding | null;
  arrangement: Arrangement;
  comparison: Comparison | null;
  amount: number;
  onAmount: (value: number) => void;
  onToggle: (finding: Finding) => void;
  route: RouteState;
  onRoute: () => void;
  onLook: (locus: Locus | null) => void;
};

function SidePanel({ task, scene, onTryLayout, assessment, scan, findings, selected, arrangement, comparison, amount, onAmount, onToggle, route, onRoute, onLook }: SidePanelProps) {
  const scope: CheckScope = { rulesChecked: assessment?.rules_checked ?? null, routeConfirmed: route.confirmed };
  if (task === "compare" && comparison) return <ComparePanel comparison={comparison} amount={amount} onAmount={onAmount} scope={scope} />;
  if (task === "arrange") return <ArrangePanel arrangement={arrangement} fallbackFindings={findings} scope={scope} />;
  if (task === "route") return <RoutePanel route={route} />;
  return (
    <>
      <AskBox scanId={scan.id} revision={scene.revision} onLook={onLook} onTry={onTryLayout} />
      <SimulationPanel key={`${scan.id}-${scene.revision}`} scanId={scan.id} scene={scene} onTryLayout={onTryLayout} />
      <FindingsPanel scan={scan} scene={scene} assessment={assessment} findings={findings} selected={selected} onToggle={onToggle} onTryLayout={onTryLayout} route={route} onRoute={onRoute} />
    </>
  );
}

type FindingsPanelProps = Pick<SidePanelProps, "scan" | "scene" | "assessment" | "findings" | "selected" | "onToggle" | "onTryLayout" | "route" | "onRoute">;

function RoutePrompt({ onRoute }: { onRoute: () => void }) {
  return (
    <div className="flex flex-col items-start gap-3 px-3">
      <p className="font-medium">Show us where customers go, and we&apos;ll check every path they take.</p>
      <Button variant="primary" onClick={onRoute}>
        <MapPin size={18} weight="bold" aria-hidden />
        Mark the customer route
      </Button>
    </div>
  );
}

/** Above the findings: confirm the route first, then let the loop fix what furniture can. */
function NextStep({ scan, scene, findings, route, onRoute, onTryLayout }: Omit<FindingsPanelProps, "assessment" | "selected" | "onToggle">) {
  if (!route.confirmed) return scan.state === "ready" ? <RoutePrompt onRoute={onRoute} /> : null;
  if (!hasProblems(findings)) return null;
  return <LoopRun key={scene.revision} scanId={scan.id} revision={scene.revision} onTry={onTryLayout} />;
}

function FindingsPanel({ scan, scene, assessment, findings, selected, onToggle, onTryLayout, route, onRoute }: FindingsPanelProps) {
  if (assessment === null && isWorking(scan)) {
    return <p className="px-3 font-medium" role="status">Checking this layout</p>;
  }
  return (
    <div className="flex flex-col gap-5">
      <NextStep scan={scan} scene={scene} findings={findings} route={route} onRoute={onRoute} onTryLayout={onTryLayout} />
      {findings.length === 0 ? (
        <p className="px-3 text-ink-muted">{scanStatus(scan, assessment, route.confirmed)}</p>
      ) : (
        <FindingsList
          groups={groupFindings(findings)}
          selectedId={selected?.id ?? null}
          onSelect={onToggle}
          extra={(finding) => <FixSuggestion scanId={scan.id} scene={scene} finding={finding} onTry={onTryLayout} />}
        />
      )}
    </div>
  );
}

type Picked = { id: string; label: string };

/** The piece last tapped in the model, read from the current scene so a relabel shows at once. */
function usePicked(scene: SceneGraph) {
  const [picked, setPicked] = useState<Picked | null>(null);
  const node = picked ? scene.nodes.find((candidate) => candidate.id === picked.id) ?? null : null;
  return { setPicked, node, label: node?.label ?? picked?.label ?? null };
}

const hasProblems = (findings: Finding[]) => findings.some((finding) => finding.outcome === "problem");

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

function useWorkspaceVisuals(props: WorkspaceProps, findings: Finding[], task: Task, amount: number, focus: Focus | null, mode: ViewMode, setDragging: (on: boolean) => void) {
  const arrangement = useArrangement(props.scan.id, props.scene);
  const comparison = comparisonFor(arrangement, props.scene, findings, props.previous);
  const shown = task === "compare" && comparison ? interpolateLayout(comparison.before, comparison.after, amount) : arrangement.shown;
  const pose = useMemo(() => poseFor(props.scene, focus, mode), [props.scene, focus, mode]);
  const handlers = useArrangeHandlers(task === "arrange", arrangement, setDragging);
  const route = useRoute(props.scan.id, props.scenario, props.suggestedScenario);
  return { arrangement, comparison, shown, pose, handlers, route, routeHandles: useRouteHandles(task, route, setDragging) };
}

function useWorkspaceActions(findings: Finding[], scene: SceneGraph, arrangement: Arrangement, setSelected: (finding: Finding | null | ((current: Finding | null) => Finding | null)) => void, setAsked: (focus: Focus | null) => void, setPicked: (picked: Picked | null) => void, setTask: (task: Task) => void, setMode: (mode: ViewMode) => void, setAmount: (amount: number) => void) {
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
  const look = (locus: Locus | null) => { setSelected(null); setAsked(locus ? focusOnLocus(locus) : null); };
  return { clear, selectNode, toggle, showView, switchTask, tryLayout, look };
}

function EvidenceToggle({ available, shown, onToggle }: { available: boolean; shown: boolean; onToggle: () => void }) {
  if (!available) return null;
  return <Button variant="chip" aria-pressed={shown} onClick={onToggle}>
    <Eye size={16} weight="bold" aria-hidden />
    {shown ? "Hide scan evidence" : "Show scan evidence"}
  </Button>;
}

function WallToggle({ cut, onToggle }: { cut: boolean; onToggle: () => void }) {
  return <Button variant="chip" aria-pressed={!cut} onClick={onToggle}>{cut ? "Show full walls" : "Cut away walls"}</Button>;
}

function GeometryDownload({ url, hasMoves, comparing }: { url: string | null; hasMoves: boolean; comparing: boolean }) {
  if (!url || hasMoves || comparing) return null;
  return <a href={url} download="room.glb" className="rounded-lg bg-sheet px-3 py-2 text-sm font-medium text-ink-muted hover:text-ink">Export GLB</a>;
}

type WorkspaceBodyProps = WorkspaceProps & {
  findings: Finding[]; task: Task; selected: Finding | null; focus: Focus | null; mode: ViewMode; picked: ReturnType<typeof usePicked>; dragging: boolean; amount: number; setAmount: (amount: number) => void; showScanEvidence: boolean; setShowScanEvidence: (value: boolean | ((current: boolean) => boolean)) => void; visuals: ReturnType<typeof useWorkspaceVisuals>; actions: ReturnType<typeof useWorkspaceActions>;
};

function WorkspaceBody({ scan, scene, exported, assessment, glbUrl, lidarUrl, findings, task, selected, focus, mode, picked, dragging, amount, setAmount, showScanEvidence, setShowScanEvidence, visuals, actions }: WorkspaceBodyProps) {
  const [cutWalls, setCutWalls] = useState(true);
  const evidenceAvailable = lidarUrl !== null && scene.revision === 0 && task !== "arrange" && task !== "compare";
  const displayedLidarUrl = capturedMeshUrl(lidarUrl, scene.revision, showScanEvidence && evidenceAvailable);
  const activeMode = selected ? null : mode;
  return <>
    <RefreshWhile pending={assessment === null && isWorking(scan)} />
    <div className="grid h-dvh grid-cols-[minmax(0,1fr)] grid-rows-[auto_minmax(16rem,45dvh)_1fr] lg:grid-cols-[minmax(0,1fr)_24rem] lg:grid-rows-[auto_1fr]">
      <WorkspaceHeader scan={scan} task={task} canCompare={visuals.comparison !== null} onTask={actions.switchTask} />
      <section className="relative min-h-0 touch-none overflow-hidden lg:rounded-tr-2xl" aria-label="Shop model">
        <Viewer scene={visuals.shown} exported={exported} arrange={visuals.handlers} route={visuals.routeHandles} dragging={dragging} cutWalls={cutWalls} glbUrl={glbUrl} lidarUrl={displayedLidarUrl} pose={visuals.pose} selected={task === "findings" ? focus : null} onSelectNode={actions.selectNode} onClearSelection={actions.clear} />
        <PickedObject
          scanId={scan.id}
          revision={scene.revision}
          label={picked.label}
          node={picked.node}
          editable={canMarkCounter(task, scan)}
        />
        <div className="absolute bottom-4 left-4 right-4 flex flex-wrap gap-2">
          <Button variant="chip" aria-pressed={activeMode === "overview"} onClick={() => actions.showView("overview")}><ArrowsOutCardinal size={16} weight="bold" aria-hidden />Whole shop</Button>
          <Button variant="chip" aria-pressed={activeMode === "top"} onClick={() => actions.showView("top")}><SquareHalfBottom size={16} weight="bold" aria-hidden />From above</Button>
          <WallToggle cut={cutWalls} onToggle={() => setCutWalls((current) => !current)} />
          <GeometryDownload url={glbUrl} hasMoves={visuals.arrangement.hasMoves} comparing={task === "compare"} />
          <EvidenceToggle available={evidenceAvailable} shown={showScanEvidence} onToggle={() => setShowScanEvidence((current) => !current)} />
        </div>
      </section>
      <aside className="min-h-0 overflow-y-auto px-3 pb-10 pt-4 lg:pt-0">
        <SidePanel task={task} scene={scene} onTryLayout={actions.tryLayout} assessment={assessment} scan={scan} findings={findings} selected={selected} arrangement={visuals.arrangement} comparison={visuals.comparison} amount={amount} onAmount={setAmount} onToggle={actions.toggle} route={visuals.route} onRoute={() => actions.switchTask("route")} onLook={actions.look} />
      </aside>
    </div>
  </>;
}

export function Workspace(props: WorkspaceProps) {
  const findings = useMemo(() => props.assessment?.findings ?? [], [props.assessment]);
  const { selected, setSelected, setAsked, focus } = useFocus();
  const [mode, setMode] = useState<ViewMode>("overview");
  const picked = usePicked(props.scene);
  const [task, setTask] = useState<Task>("findings");
  const [dragging, setDragging] = useState(false);
  const [amount, setAmount] = useState(1);
  const [showScanEvidence, setShowScanEvidence] = useState(false);
  const visuals = useWorkspaceVisuals(props, findings, task, amount, focus, mode, setDragging);
  const actions = useWorkspaceActions(findings, props.scene, visuals.arrangement, setSelected, setAsked, picked.setPicked, setTask, setMode, setAmount);
  useKeyboard(task, visuals.arrangement, actions.clear);
  return <WorkspaceBody {...props} findings={findings} task={task} selected={selected} focus={focus} mode={mode} picked={picked} dragging={dragging} amount={amount} setAmount={setAmount} showScanEvidence={showScanEvidence} setShowScanEvidence={setShowScanEvidence} visuals={visuals} actions={actions} />;
}
