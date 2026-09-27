import { notFound } from "next/navigation";
import { RefreshWhile } from "@/components/RefreshWhile";
import { Workspace } from "@/components/workspace/Workspace";
import { getAssessment, getCapturedSplats, getEvidence, getRooms, getScan, getScenario, getScenarioSuggestion, getScene, getTextureStatus, headSceneGlb, sceneGlbUrl } from "@/lib/api";
import { scanStatus } from "@/lib/scan-status";
import type { Scan, SceneGraph } from "@/types/contracts";
import type { RoomGroup } from "@/lib/room-groups";
import { isTeam, requireSession } from "@/lib/session";

export const dynamic = "force-dynamic";

/**
 * The walks of a combined scan, each with the photographed mesh it was scanned as.
 *
 * Placing four walks against each other means recognising them, and the boxes
 * are white and featureless: every room looks like every other. Each walk was
 * uploaded as a scan of its own and photographed, so the model carrying those
 * photographs comes along and the owner drags a room they can see.
 *
 * The photographed model rather than the raw scanned surface. Four captures of
 * one library floor are about a hundred and twenty megabytes of LiDAR and some
 * sixteen million faces between them, which no browser draws; the same four
 * models wearing the same photographs are twelve megabytes and a few thousand.
 */
async function roomsFor(scanId: string, revision: number): Promise<RoomGroup[]> {
  const rooms = (await getRooms(scanId, revision))?.rooms ?? [];
  return Promise.all(rooms.map(withCapturedMesh));
}

async function withCapturedMesh(room: RoomGroup): Promise<RoomGroup> {
  if (!room.source_scan_id) return room;
  const status = await getTextureStatus(room.source_scan_id, 0);
  return { ...room, scan_glb_url: status?.build?.glb_url ?? null };
}

/** Whether a GLB exists, and the revision whose layout it was exported from. */
async function glbStatus(scanId: string) {
  const response = await headSceneGlb(scanId);
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
      <p className="mt-4 text-lg text-ink-muted">{scanStatus(scan, null, false)}</p>
      <RefreshWhile pending={scan.state !== "failed"} />
    </main>
  );
}

export default async function ShopPage({ params }: PageProps<"/scans/[scanId]">) {
  const session = await requireSession();
  const { scanId } = await params;
  const scan = await getScan(scanId);
  if (!scan) notFound();
  const [scene, geometry] = await Promise.all([getScene(scanId), glbStatus(scanId)]);
  if (!scene) return <NotMeasuredYet scan={scan} />;
  const glbRevision = geometry.revision;

  const [assessment, exported, previous, scenario, textureStatus, capturedSplats] = await Promise.all([
    getAssessment(scanId, scene.revision),
    loadExported(scanId, scene, glbRevision),
    scene.revision === 0 ? null : loadPrevious(scanId, scene.revision - 1),
    getScenario(scanId),
    getTextureStatus(scanId, scene.revision),
    getCapturedSplats(scanId, scene.revision),
  ]);
  const evidence = await getEvidence(scanId);
  const suggestedScenario = scenario ? null : await getScenarioSuggestion(scanId);
  const rooms = await roomsFor(scanId, scene.revision);
  return (
    <><RefreshWhile pending={geometry.pending} /><Workspace
      scan={scan}
      scene={scene}
      exported={exported}
      assessment={assessment}
      previous={previous}
      glbUrl={glbRevision === null ? null : sceneGlbUrl(scanId, glbRevision)}
      textureStatus={textureStatus}
      capturedSplats={capturedSplats}
      scenario={scenario}
      suggestedScenario={suggestedScenario}
      lidarUrl={scan.artifacts.some((artifact) => artifact.kind === "lidar_mesh") ? `/api/scans/${scanId}/lidar-mesh` : null}
      rooms={rooms}
      evidence={evidence}
      team={isTeam(session)}
    /></>
  );
}
