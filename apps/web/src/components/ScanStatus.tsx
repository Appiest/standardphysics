import { ActivitySpinner } from "@/components/ui/ActivitySpinner";
import { scanStatus } from "@/lib/scan-status";
import type { Assessment, Scan } from "@/types/contracts";

export function ScanStatus({ scan, assessment, routeConfirmed, className = "" }: {
  scan: Scan;
  assessment: Assessment | null;
  routeConfirmed: boolean;
  className?: string;
}) {
  if (scan.state === "failed") return <ActivitySpinner className={className} />;
  return <span className={className}>{scanStatus(scan, assessment, routeConfirmed)}</span>;
}
