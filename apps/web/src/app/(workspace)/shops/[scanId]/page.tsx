import { headers } from "next/headers";
import { notFound } from "next/navigation";
import { OwnerView } from "@/components/owner/OwnerView";
import { RefreshWhile } from "@/components/RefreshWhile";
import { getAssessment, getChecklist, getJourney, getPathSuggestion, getRequests, getScan, getScenario, getScene, readyGlbUrl } from "@/lib/api";
import { isAppUserAgent } from "@/lib/native-bridge";
import { defaultPlaces, isWaiting } from "@/lib/owner-journey";
import { requireSession } from "@/lib/session";
import type { Journey, Scan, Scenario, SceneGraph } from "@/types/contracts";

export const dynamic = "force-dynamic";

const NO_CHECKLIST = { items: [], done: 0, total: 0 };

/**
 * The sample shop's model file is a fixture with no surfaces, which draws as
 * flat grey; its measured pieces read better. A scanned shop's model shows the
 * real furniture.
 */
function hasDrawnSurfaces(scan: Scan): boolean {
  return scan.device_model !== SAMPLE_DEVICE;
}

const SAMPLE_DEVICE = "Sample";

function modelUrl(scan: Scan, scene: SceneGraph | null): Promise<string | null> | null {
  return scene && hasDrawnSurfaces(scan) ? readyGlbUrl(scan.id) : null;
}

/** The suggested path, fetched only when the owner is about to shape it. */
function pathToShape(scanId: string, journey: Journey, places: string[]): Promise<Scenario | null> | null {
  const shaping = journey.next_step.kind === "counter" || journey.next_step.kind === "path";
  return shaping ? getPathSuggestion(scanId, places) : null;
}

/** The owner's view of one shop: the one next step, then the results, the checklist, sharing and the tools. */
export default async function OwnerShopPage({ params }: PageProps<"/shops/[scanId]">) {
  const session = await requireSession();
  const { scanId } = await params;
  const [scan, journey] = await Promise.all([getScan(scanId), getJourney(scanId)]);
  if (!scan || !journey) notFound();
  const [requests, scene, assessment, checklist, scenario] = await Promise.all([
    getRequests(scanId), getScene(scanId), getAssessment(scanId), getChecklist(scanId), getScenario(scanId),
  ]);
  const places = defaultPlaces(requests);
  const [glbUrl, suggestedPath] = await Promise.all([modelUrl(scan, scene), pathToShape(scanId, journey, places)]);
  const embedded = isAppUserAgent((await headers()).get("user-agent"));
  return (
    <>
      <RefreshWhile pending={isWaiting(journey) || scan.state === "checking"} />
      <OwnerView
        scan={scan}
        journey={journey}
        requests={requests}
        scene={scene}
        glbUrl={glbUrl}
        assessment={assessment}
        checklist={checklist ?? NO_CHECKLIST}
        suggestedPath={suggestedPath}
        scenario={scenario}
        defaultPlaces={places}
        guest={session.guest}
        embedded={embedded}
      />
    </>
  );
}
