import { ArrowLeft } from "@phosphor-icons/react/dist/ssr";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PrintButton } from "@/components/PrintButton";
import { ReportDocument } from "@/components/report/ReportDocument";
import { getReport } from "@/lib/api";
import { isTeam, requireSession } from "@/lib/session";

export const dynamic = "force-dynamic";

export default async function ReportPage({ params }: PageProps<"/scans/[scanId]/report">) {
  const session = await requireSession();
  const { scanId } = await params;
  const report = await getReport(scanId);
  if (!report) notFound();

  return (
    <ReportDocument
      report={report}
      showScope={isTeam(session)}
      toolbar={
        <>
          <Link href={`/scans/${scanId}`} className="-ms-2 flex items-center gap-2 rounded-lg p-2 text-ink-muted hover:bg-ink/5 hover:text-ink">
            <ArrowLeft size={18} weight="bold" aria-hidden />
            Back to the shop
          </Link>
          <PrintButton />
        </>
      }
    />
  );
}
