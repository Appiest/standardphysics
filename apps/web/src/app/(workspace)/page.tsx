import { SheetFrame } from "@/components/blueprint/SheetFrame";
import { FeaturedSheet } from "@/components/home/FeaturedSheet";
import { HomeTitleBlock } from "@/components/home/HomeTitleBlock";
import { SheetIndex } from "@/components/home/SheetIndex";
import { loadShopSheet, sheetNumber } from "@/components/home/shopSheet";
import { listScans } from "@/lib/api";
import { isTeam, requireSession } from "@/lib/session";

export const dynamic = "force-dynamic";

function EmptySheet() {
  return (
    <div className="flex h-72 flex-col items-center justify-center gap-2 border border-dashed border-ink px-6 text-center sm:h-[26rem]">
      <p className="heading-display text-2xl">No drawings yet</p>
      <p className="max-w-sm text-ink-muted text-pretty">Scan your shop with an iPhone and its floor plan shows up here, measured to the inch.</p>
    </div>
  );
}

export default async function ShopsPage() {
  const session = await requireSession();
  const team = isTeam(session);
  const sheets = await Promise.all((await listScans()).map((scan) => loadShopSheet(scan, team)));
  const [featured, ...others] = sheets;

  return (
    <main className="flex min-h-dvh px-3 py-3 sm:px-5 sm:py-5">
      <SheetFrame columns={8} rows={6} className="flex-1">
        <div className="grid gap-8 px-4 py-6 sm:px-8 sm:py-8 lg:min-h-full lg:grid-cols-[minmax(0,1fr)_20rem] lg:gap-10">
          <div className="flex min-w-0 flex-col gap-6">
            <h1 className="heading-display text-5xl sm:text-7xl">{sheets.length > 1 ? "Your shops" : "Your shop"}</h1>
            {featured ? <FeaturedSheet sheet={featured} sheetNumber={sheetNumber(0)} /> : <EmptySheet />}
            {others.length > 0 && <SheetIndex sheets={others} firstIndex={1} />}
          </div>
          <HomeTitleBlock session={session} className="lg:self-end" />
        </div>
      </SheetFrame>
    </main>
  );
}
