import Link from "next/link";
import { notFound } from "next/navigation";
import { SimulationReplayPlayer } from "@/components/workspace/SimulationReplayPlayer";
import { getScan, getSimulationReplay } from "@/lib/api";
import type { SimulationReplay } from "@/types/contracts";
import { requireSession } from "@/lib/session";

export const dynamic = "force-dynamic";

function CampaignEvidence({ replay }: { replay: SimulationReplay }) {
  return <details className="rounded-xl bg-sheet p-5">
    <summary className="cursor-pointer font-medium">How these runs were measured</summary>
    <div className="mt-4 space-y-4 text-sm text-ink-muted">
      <p>{replay.evaluations.toLocaleString("en-US")} distinct start, approach, target, task and body-profile checks reuse {replay.connectivity_builds} precomputed connectivity maps. They cover {replay.unique_layouts} scanned layout. They are not independent reconstructions of the room.</p>
      <p>Tasks came from {replay.task_source}. TypeSafe selected the task order through {replay.typesafe_calls} model calls.</p>
      <p>{replay.selection}</p>
      <ul className="list-disc space-y-2 ps-5">{replay.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul>
      <p>Estimates within 5 cm of the reach limit need physical measurement. These checks do not estimate lawsuit probability or certify ADA compliance.</p>
      <details>
        <summary className="cursor-pointer">Source fingerprints</summary>
        <dl className="mt-3 space-y-3 break-all">
          <div><dt>Room snapshot</dt><dd className="measurement">{replay.graph_hash}</dd></div>
          <div><dt>Campaign</dt><dd className="measurement">{replay.report_sha256}</dd></div>
          <div><dt>Video</dt><dd className="measurement">{replay.video_sha256}</dd></div>
        </dl>
      </details>
    </div>
  </details>;
}

export default async function ReplayPage({ params, searchParams }: PageProps<"/scans/[scanId]/replay">) {
  await requireSession();
  const { scanId } = await params;
  const query = await searchParams;
  const revision = Number(query.revision ?? "0");
  if (!Number.isSafeInteger(revision) || revision < 0 || Array.isArray(query.revision)) notFound();
  const scan = await getScan(scanId);
  if (!scan) notFound();
  const replay = await getSimulationReplay(scanId, revision);
  return <main className="mx-auto max-w-6xl space-y-8 px-4 py-8 sm:px-8">
    <Link className="inline-block py-2 text-accent underline underline-offset-4" href={`/scans/${scanId}`}>Back to {scan.name}</Link>
    <div className="space-y-3">
      <h1 className="heading-display text-3xl">{scan.name} recorded runs</h1>
      <p className="text-ink-muted">Recorded against room revision {revision}.</p>
    </div>
    {replay ? <>
      <p className="text-lg">{replay.evaluations.toLocaleString("en-US")} geometric checks. {replay.chapters.length} selected journeys in eye-level and third-person views.</p>
      <SimulationReplayPlayer replay={replay} />
      <CampaignEvidence replay={replay} />
    </> : <p>No recording is saved for this revision. Return to the room to run route trials.</p>}
  </main>;
}
