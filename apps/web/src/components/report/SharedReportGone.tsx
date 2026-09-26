import { LinkBreak } from "@phosphor-icons/react/dist/ssr";
import { SheetFrame } from "@/components/blueprint/SheetFrame";
import { headlineAndRest } from "./sharedReport";
import { Wordmark } from "./Wordmark";

/** A report link that no longer opens anything, in the API's own words. */
export function SharedReportGone({ message }: { message: string }) {
  const { headline, rest } = headlineAndRest(message);
  return (
    <main className="flex min-h-dvh px-3 py-3 sm:px-5 sm:py-5">
      <SheetFrame columns={8} rows={6} className="flex-1">
        <div className="flex h-full flex-col gap-10 px-4 py-6 sm:px-8 sm:py-8">
          <Wordmark />
          <div className="flex flex-1 flex-col items-start justify-center gap-5 pb-10">
            <LinkBreak size={48} className="text-ink-muted" aria-hidden />
            <h1 className="heading-display text-4xl text-balance sm:text-5xl">{headline}</h1>
            {rest && <p className="max-w-md text-lg text-ink-muted text-pretty">{rest}</p>}
          </div>
        </div>
      </SheetFrame>
    </main>
  );
}
