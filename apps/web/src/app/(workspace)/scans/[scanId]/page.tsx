import { notFound } from "next/navigation";
import { RefreshWhile } from "@/components/RefreshWhile";
import { ScanStatus } from "@/components/ScanStatus";
import { Workspace } from "@/components/workspace/Workspace";
import { getAssessment, getScan, getScenario, getScenarioSuggestion, getScene, getTextureStatus, sceneGlbUrl } from "@/lib/api";
import { API_ORIGIN } from "@/lib/api-origin";
import type { Scan, SceneGraph } from "@/types/contracts";

export const dynamic = "force-dynamic";

/** Whether a GLB exists, and the revision whose layout it was exported from. */
async function glbStatus(scanId: string) {
  const response = await fetch(`${API_ORIGIN}${sceneGlbUrl(scanId)}`, { method: "HEAD", cache: "no-store" });
  const revision = response.headers.get("X-Exported-Revision");
  return {
    revision: response.ok && revision !== null ? Number(revision) : null,
    pending: response.headers.get("X-Display-Pending") === "true",
  };
}

async function loadPrevious(scanId: string, revision: number) {
  const [scene, assessment] = await Promise.all([getScene(scanId, revision), getAssessment(scanId, revision)]);
  return scene ? { scene, assessment } : null;
}

async function loadExported(scanId: string, scene: SceneGraph, glbRevision: number | null): Promise<SceneGraph> {
  if (glbRevision === null || glbRevision === scene.revision) return scene;
  return (await getScene(scanId, glbRevision)) ?? scene;
}

function NotMeasuredYet({ scan }: { scan: Scan }) {
  return (
    <main className="mx-auto max-w-2xl px-5 py-20">
      <h1 className="heading-display text-3xl">{scan.name}</h1>
      <ScanStatus className="mt-4 text-lg text-ink-muted" scan={scan} assessment={null} routeConfirmed={false} />
      <RefreshWhile pending={scan.state !== "failed"} />
    </main>
  );
}

export default async function ShopPage({ params }: PageProps<"/scans/[scanId]">) {
  const { scanId } = await params;
  const scan = await getScan(scanId);
  if (!scan) notFound();
  const [scene, geometry] = await Promise.all([getScene(scanId), glbStatus(scanId)]);
  if (!scene) return <NotMeasuredYet scan={scan} />;
  const glbRevision = geometry.revision;

  const [assessment, exported, previous, scenario, textureStatus] = await Promise.all([
    getAssessment(scanId, scene.revision),
    loadExported(scanId, scene, glbRevision),
    scene.revision === 0 ? null : loadPrevious(scanId, scene.revision - 1),
    getScenario(scanId),
    getTextureStatus(scanId, scene.revision),
  ]);
  const suggestedScenario = scenario ? null : await getScenarioSuggestion(scanId);
  return (
    <><RefreshWhile pending={geometry.pending} /><Workspace
      scan={scan}
      scene={scene}
      exported={exported}
      assessment={assessment}
      previous={previous}
      glbUrl={glbRevision === null ? null : sceneGlbUrl(scanId, glbRevision)}
      textureStatus={textureStatus}
      scenario={scenario}
      suggestedScenario={suggestedScenario}
      lidarUrl={scan.artifacts.some((artifact) => artifact.kind === "lidar_mesh") ? `/api/scans/${scanId}/lidar-mesh` : null}
    /></>
  );
}
