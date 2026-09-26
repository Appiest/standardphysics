import type { Metadata } from "next";
import { headers } from "next/headers";
import { notFound } from "next/navigation";
import { ExampleInvite } from "@/components/owner/ExampleInvite";
import { OwnerView } from "@/components/owner/OwnerView";
import { getSharedReport } from "@/lib/api";
import { isAppUserAgent } from "@/lib/native-bridge";
import type { Journey, Scan } from "@/types/contracts";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "An example shop, Standard Physics",
  description: "What Standard Physics finds in a real layout, and how to fix it.",
};

const NO_CHECKLIST = { items: [], done: 0, total: 0 };

function exampleJourney(scan: Scan): Journey {
  return {
    scan_id: scan.id, shop_name: scan.name, stage: "results", tools_unlocked: false,
    next_step: { kind: "results", title: "See what we found", count: null },
  };
}

/** The sample shop's results, to explore before walking your own. Anyone can open it. */
export default async function ExamplePage() {
  const report = await getSharedReport("example");
  if (!report?.scene) notFound();
  const requestHeaders = await headers();
  const embedded = isAppUserAgent(requestHeaders.get("user-agent"));
  return (
    <OwnerView
      scan={report.scan}
      journey={exampleJourney(report.scan)}
      requests={[]}
      scene={report.scene}
      glbUrl={null}
      assessment={report.assessment}
      checklist={NO_CHECKLIST}
      suggestedPath={null}
      scenario={report.scenario}
      defaultPlaces={[]}
      guest={false}
      embedded={embedded}
      readOnly
      footer={<ExampleInvite key="invite" />}
    />
  );
}
