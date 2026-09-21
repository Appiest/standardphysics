import { ArrowRight, ArrowUp } from "@phosphor-icons/react/dist/ssr";
import Link from "next/link";
import { InkedFloorPlan } from "@/components/blueprint/InkedFloorPlan";
import { SHEET_GRID_CLASS, SheetField } from "@/components/blueprint/SheetField";
import { countNeedingAttention } from "@/lib/findings";
import { scanStatus } from "@/lib/scan-status";
import type { ShopSheet } from "./shopSheet";

const dateFormat = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" });

function toFixValue({ assessment }: ShopSheet) {
  if (!assessment || assessment.rules_checked === 0) return "Not checked yet";
  return String(countNeedingAttention(assessment.findings));
}

function PlanPlaceholder({ sheet }: { sheet: ShopSheet }) {
  return (
    <div className="flex h-72 items-center justify-center px-6 text-center text-ink-muted sm:h-[26rem]">
      {scanStatus(sheet.scan, sheet.assessment, sheet.hasScenario)}
    </div>
  );
}

export function FeaturedSheet({ sheet, sheetNumber }: { sheet: ShopSheet; sheetNumber: string }) {
  const { scan, scene, assessment, hasScenario } = sheet;
  const checkedOn = new Date(assessment?.created_at ?? scan.created_at);
  const dateLabel = assessment ? "Checked" : "Scanned";
  return (
    <Link href={`/scans/${scan.id}`} className="group block border border-ink bg-paper transition-colors duration-150 hover:bg-sheet">
      <div className="flex items-center justify-between border-b border-ink px-4 py-2">
        <span className="font-semibold tabular-nums">{sheetNumber}</span>
        <span className="flex items-center gap-1 text-sm text-ink-muted">
          <ArrowUp size={14} weight="bold" aria-hidden />
          North
        </span>
      </div>
      {scene ? <InkedFloorPlan scene={scene} className="drafting-grid h-80 sm:h-[28rem]" /> : <PlanPlaceholder sheet={sheet} />}
      <dl className={`${SHEET_GRID_CLASS} border-t border-ink *:bg-paper group-hover:*:bg-sheet sm:grid-cols-[minmax(0,2fr)_1fr_1fr_auto]`}>
        <SheetField label="Shop">
          <span className="heading-display block truncate text-2xl">{scan.name}</span>
          <span className="block text-sm font-normal text-ink-muted text-pretty">{scanStatus(scan, assessment, hasScenario)}</span>
        </SheetField>
        <SheetField label={dateLabel}>{dateFormat.format(checkedOn)}</SheetField>
        <SheetField label="To fix">{toFixValue(sheet)}</SheetField>
        <div className="flex items-center gap-2 px-4 py-3 font-semibold">
          Open the shop
          <ArrowRight size={18} weight="bold" aria-hidden className="transition-transform duration-150 group-hover:translate-x-1" />
        </div>
      </dl>
    </Link>
  );
}
