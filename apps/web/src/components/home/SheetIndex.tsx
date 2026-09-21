import { ArrowRight } from "@phosphor-icons/react/dist/ssr";
import Link from "next/link";
import { scanStatus } from "@/lib/scan-status";
import { sheetNumber, type ShopSheet } from "./shopSheet";

export function SheetIndex({ sheets, firstIndex }: { sheets: ShopSheet[]; firstIndex: number }) {
  return (
    <section aria-labelledby="other-shops">
      <h2 id="other-shops" className="heading-display text-xl">
        Other shops
      </h2>
      <ul className="mt-3 border-t border-ink">
        {sheets.map((sheet, index) => (
          <li key={sheet.scan.id} className="border-b border-ink">
            <Link
              href={`/scans/${sheet.scan.id}`}
              className="group grid grid-cols-[4.5rem_minmax(0,1fr)_auto] items-center gap-4 px-2 py-3 transition-colors duration-150 hover:bg-sheet"
            >
              <span className="font-semibold tabular-nums">{sheetNumber(firstIndex + index)}</span>
              <span className="min-w-0">
                <span className="block truncate font-semibold">{sheet.scan.name}</span>
                <span className="block truncate text-sm text-ink-muted">{scanStatus(sheet.scan, sheet.assessment, sheet.hasScenario)}</span>
              </span>
              <ArrowRight size={18} weight="bold" aria-hidden className="transition-transform duration-150 group-hover:translate-x-1" />
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
