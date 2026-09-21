"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { accessibilityLoopRequest, LOOP_TRIALS, LOOP_WORKERS, loopResultSentence } from "@/lib/accessibility-loop";
import { getSimulation, SimulationRequestError, startSimulation } from "@/lib/simulation-client";
import { candidateMoves } from "@/lib/moves";
import type { NodeMove, SceneGraph, SimulationFeedback, SimulationStatus } from "@/types/contracts";

const POLL_MS = 2_000;

function isActive(status: SimulationStatus | null) {
  return status?.state === "queued" || status?.state === "running";
}

function failureSummary(feedback: SimulationFeedback) {
  return [
    `${feedback.clearance_failure_trials} clearance`,
    `${feedback.floor_plan_collision_trials} floor-plan collision`,
    `${feedback.mesh_collision_trials} mesh collision`,
    `${feedback.unreachable_interaction_trials} unreachable`,
    `${feedback.needs_measurement_trials} needs measurement`,
  ].filter((item) => !item.startsWith("0 ")).join(", ");
}

function Feedback({ feedback, labels }: { feedback: SimulationFeedback; labels: Map<string, string> }) {
  const blockers = feedback.blocking_node_ids.map((id) => labels.get(id) ?? id);
  return (
    <li className="space-y-1 rounded-lg bg-rule/30 p-3 text-sm">
      <p className="font-medium">{feedback.workflow_title}: {feedback.profile_title}</p>
      <p className="text-ink-muted">{feedback.passed_trials} passed out of {feedback.trials}; {failureSummary(feedback) || "no recorded failures"}.</p>
      {blockers.length > 0 && <p className="text-ink-muted">Blockers: {blockers.join(", ")}</p>}
    </li>
  );
}

function message(reason: unknown, fallback: string) {
  return reason instanceof Error ? reason.message : fallback;
}

function SimulationControls({ active, onRun }: {
  active: boolean;
  onRun: () => void;
}) {
  return <Button variant="primary" onClick={onRun} disabled={active}>
    {active ? "Loop running" : "Start loop"}
  </Button>;
}

function TrialProgress({ status }: { status: SimulationStatus | null }) {
  if (!status || !isActive(status)) return null;
  const completed = Math.min(status.completed, status.samples);
  const percent = status.samples > 0 ? Math.round((completed / status.samples) * 100) : 0;
  const progressText = `${completed} of ${status.samples} tests finished in this batch`;
  return <div className="space-y-1" role="status">
    <div
      aria-label="Route trial progress"
      aria-valuemax={status.samples}
      aria-valuemin={0}
      aria-valuenow={completed}
      aria-valuetext={progressText}
      className="h-2 w-full overflow-hidden rounded-full bg-rule"
      role="progressbar"
    >
      <div
        className="h-full rounded-full bg-accent transition-[width] duration-500 motion-reduce:transition-none"
        style={{ width: `${percent}%` }}
      />
    </div>
    <p className="flex justify-between gap-3 text-sm text-ink-muted">
      <span>{completed === 0 && status.state === "queued" ? "Waiting to start the loop" : `${completed} of ${status.samples} tests finished in this batch.`}</span>
      <span aria-hidden>{percent}%</span>
    </p>
  </div>;
}

function downloadCandidate(graph: SceneGraph) {
  const href = URL.createObjectURL(new Blob([JSON.stringify(graph, null, 2)], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = href;
  link.download = "standardphysics-candidate-layout.json";
  link.click();
  URL.revokeObjectURL(href);
}

function ResultIntroduction({ result }: { result: NonNullable<SimulationStatus["result"]> }) {
  const preview = result.preview ? "This is an unverified preview for review; it does not apply any layout changes." : "No layout changes were applied.";
  const mesh = result.mesh_checked ? "Measured mesh collisions were screened." : "Measured mesh collision screening was unavailable.";
  return <>
    <p className={result.converged ? "font-medium" : "font-medium text-problem"}>{loopResultSentence(result)}</p>
    <p className="text-ink-muted">Ran {result.loop_cycles} full {result.loop_cycles === 1 ? "batch" : "batches"}. Rules checked: {result.rules_checked} of {result.rules_total}.</p>
    <p className="text-ink-muted">Provider calls: {result.typesafe_calls} TypeSafe and {result.astra_calls} Astra. Deterministic exhaustive evaluations: {result.exhaustive_evaluations.toLocaleString()}.</p>
    <p className="text-ink-muted">{preview} {mesh}</p>
  </>;
}

function PhysicsResults({ result, labels }: { result: NonNullable<SimulationStatus["result"]>; labels: Map<string, string> }) {
  const physics = result.physics;
  if (!physics) return null;
  const barriers = physics.observations.filter((item) => item.status !== "clear");
  const blockedRoutes = physics.routes.filter((route) => !route.reachable);
  return <details className="rounded-lg bg-rule/30 p-3" open>
    <summary className="cursor-pointer font-medium">Wheelchair physics and environment routes</summary>
    <div className="mt-3 space-y-2 text-sm">
      <p className="text-ink-muted">One-inch analysis; {physics.mesh_triangles_checked.toLocaleString()} mesh triangles, {physics.surface_samples.toLocaleString()} low-surface samples, {physics.seats_found} seats, {physics.cashiers_found} service points, and {physics.exits_found} exits.</p>
      <p className="text-ink-muted">{physics.routes.length} customer routes screened between every door, opening, and piece of furniture; {blockedRoutes.length} were unreachable.</p>
      {barriers.length > 0 && <ul className="space-y-2">{barriers.map((item, index) => {
        const nodes = item.node_ids.map((id) => labels.get(id) ?? id);
        const value = item.measured_value === null ? "" : `: ${item.measured_value.toFixed(2)} ${item.unit ?? ""}`;
        return <li key={`${item.kind}-${index}`} className="rounded-md border border-rule p-2">
          <p className="font-medium">{item.title}{value}</p>
          <p className="text-ink-muted">{item.status.replaceAll("_", " ")}{nodes.length > 0 ? `; ${nodes.join(", ")}` : ""}</p>
        </li>;
      })}</ul>}
    </div>
  </details>;
}

function ResultFeedback({ feedback, labels }: { feedback: SimulationFeedback[]; labels: Map<string, string> }) {
  if (feedback.length === 0) return null;
  return <details className="rounded-lg bg-rule/30 p-3"><summary className="cursor-pointer font-medium">Profile failures and blockers</summary><ul className="mt-3 space-y-2">{feedback.map((item) => <Feedback key={`${item.workflow_title}-${item.profile_title}`} feedback={item} labels={labels} />)}</ul></details>;
}

function ResultLimitations({ limitations }: { limitations: string[] }) {
  if (limitations.length === 0) return null;
  return <ul className="list-disc space-y-1 pl-5 text-ink-muted">{limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul>;
}

function CandidateDownload({ graph }: { graph: SceneGraph | null }) {
  if (!graph) return null;
  return <Button variant="quiet" onClick={() => downloadCandidate(graph)}>Download candidate layout JSON</Button>;
}

function AstraRedesign({ result }: { result: NonNullable<SimulationStatus["result"]> }) {
  if (!result.redesign_model) return null;
  return <div className="space-y-1 rounded-lg bg-rule/30 p-3 text-sm">
    <p className="font-medium">Astra repairs: {result.adaptive_rounds.filter((round) => round.accepted).length} accepted</p>
    <p className="text-ink-muted">Model: {result.redesign_model}. Attempts: {result.adaptive_rounds.length}.</p>
    {result.redesign_reasons.length > 0 && <ul className="list-disc space-y-1 pl-5 text-ink-muted">{result.redesign_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>}
  </div>;
}

function SimulationResults({ status, labels, scene, onTryLayout }: { status: SimulationStatus | null; labels: Map<string, string>; scene: SceneGraph; onTryLayout: (moves: NodeMove[]) => void }) {
  if (!status?.result) return null;
  const moves = status.result.recommended_graph && candidateMoves(scene, status.result.recommended_graph);
  return <div className="space-y-3 text-sm">
    <ResultIntroduction result={status.result} />
    <PhysicsResults result={status.result} labels={labels} />
    <ResultFeedback feedback={status.result.feedback} labels={labels} />
    <ResultLimitations limitations={status.result.limitations} />
    <AstraRedesign result={status.result} />
    {moves && moves.length > 0 && <Button variant="quiet" onClick={() => onTryLayout(moves)}>Preview candidate in room</Button>}
    <CandidateDownload graph={status.result.recommended_graph} />
  </div>;
}

function SimulationMessages({ status, error }: { status: SimulationStatus | null; error: string | null }) {
  const cycle = status?.cycle ?? 0;
  return <>
    {isActive(status) && cycle > 0 && <p className="text-sm text-ink-muted">Pass {cycle}: testing the layout shown in the room.</p>}
    {status?.error && <p role="alert" className="text-sm text-problem">{status.error}</p>}
    {error && <p role="alert" className="text-sm text-problem">{error}</p>}
  </>;
}

function useSimulation(scanId: string, revision: number, scene: SceneGraph, onPreviewLayout: (moves: NodeMove[]) => void) {
  const [status, setStatus] = useState<SimulationStatus | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const previewedLayout = useRef<string | null>(null);
  const active = isActive(status);

  useEffect(() => {
    if (!status?.candidate_graph) return;
    const moves = candidateMoves(scene, status.candidate_graph);
    if (!moves) return;
    const key = JSON.stringify(moves);
    if (key === previewedLayout.current) return;
    previewedLayout.current = key;
    onPreviewLayout(moves);
  }, [status?.candidate_graph, scene, onPreviewLayout]);

  useEffect(() => {
    let stale = false;
    let pending = false;
    async function refresh() {
      if (pending) return;
      pending = true;
      try {
        const next = await getSimulation(scanId, revision);
        if (!stale) setStatus(next);
      } catch (reason) {
        if (!stale && !(reason instanceof SimulationRequestError && reason.status === 404)) {
          setError(message(reason, "We couldn't update the loop."));
        }
      }
      finally { pending = false; }
    }
    void refresh();
    if (!active) return () => { stale = true; };
    const timer = setInterval(() => {
      void refresh();
    }, POLL_MS);
    return () => { stale = true; clearInterval(timer); };
  }, [scanId, revision, active]);

  async function run() {
    setError(null);
    setStarting(true);
    try {
      setStatus(await startSimulation(scanId, accessibilityLoopRequest(revision)));
    } catch (reason) {
      setError(message(reason, "We couldn't start the loop."));
    } finally {
      setStarting(false);
    }
  }

  return { status, starting, error, run };
}

export function SimulationPanel({ scanId, scene, onTryLayout, onPreviewLayout }: { scanId: string; scene: SceneGraph; onTryLayout: (moves: NodeMove[]) => void; onPreviewLayout: (moves: NodeMove[]) => void }) {
  const simulation = useSimulation(scanId, scene.revision, scene, onPreviewLayout);
  const labels = useMemo(() => new Map(scene.nodes.map((node) => [node.id, node.label])), [scene.nodes]);
  return (
    <section className="space-y-3 px-3 py-4" aria-label="Accessibility loop">
      <div className="space-y-1">
        <h2 className="font-semibold">Accessibility loop</h2>
        <p className="text-sm text-ink-muted">Runs {LOOP_TRIALS.toLocaleString("en-US")} tests across {LOOP_WORKERS} parallel measurement workers. Astra repairs failures, then the loop retests until zero violations or the safety limit.</p>
      </div>
      <SimulationControls active={isActive(simulation.status) || simulation.starting} onRun={simulation.run} />
      <TrialProgress status={simulation.status} />
      <SimulationMessages status={simulation.status} error={simulation.error} />
      <SimulationResults status={simulation.status} labels={labels} scene={scene} onTryLayout={onTryLayout} />
    </section>
  );
}
