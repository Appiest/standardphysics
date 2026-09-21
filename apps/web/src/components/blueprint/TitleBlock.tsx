import { ArrowCounterClockwise, DownloadSimple, Eraser } from "@phosphor-icons/react";
import { Button } from "@/components/ui/Button";
import { SHEET_GRID_CLASS, SheetField } from "./SheetField";
import { SHEET_SCALE_LABEL } from "@/lib/schematic-brush/vocabulary";

interface TitleBlockProps {
  penZone: string | null;
  hasDrawn: boolean;
  onClear: () => void;
  onReplay: () => void;
  onSave: () => void;
}

const issuedOn = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" }).format(new Date());

export function TitleBlock({ penZone, hasDrawn, onClear, onReplay, onSave }: TitleBlockProps) {
  return (
    <section
      data-ink-avoid
      aria-label="Title block"
      className="pointer-events-auto absolute right-0 bottom-0 w-full border-t border-ink bg-sheet sm:w-96 sm:border-l"
    >
      <div className="border-b border-ink px-4 py-2 sm:py-3">
        <h1 className="heading-display text-xl text-balance">Standard Physics</h1>
        <p className="text-ink-muted">Accessible route study</p>
      </div>
      <dl className={`${SHEET_GRID_CLASS} hidden grid-cols-2 border-b border-ink *:bg-sheet sm:grid`}>
        <SheetField label="Sheet">A-101</SheetField>
        <SheetField label="Scale">{SHEET_SCALE_LABEL}</SheetField>
        <SheetField label="Pen zone">{penZone ?? "Off sheet"}</SheetField>
        <SheetField label="Issued">{issuedOn}</SheetField>
      </dl>
      <div className="flex flex-wrap items-center gap-1 px-1 py-1 sm:pb-0 sm:pt-2">
        <Button squared onClick={onClear}>
          <Eraser size={18} aria-hidden />
          Clear sheet
        </Button>
        <Button squared onClick={onReplay}>
          <ArrowCounterClockwise size={18} aria-hidden />
          Replay
        </Button>
        <Button squared onClick={onSave}>
          <DownloadSimple size={18} aria-hidden />
          Save PNG
        </Button>
      </div>
      <p className="hidden px-4 pt-2 pb-3 text-sm text-ink-muted text-pretty sm:block">
        {hasDrawn ? (
          <>
            Brush adapted from{" "}
            <a className="underline decoration-dotted underline-offset-2 hover:text-ink" href="https://github.com/blakeshao/p5-playground">
              Blake Shao&rsquo;s p5 playground
            </a>
            .
          </>
        ) : (
          "Drag anywhere on the sheet to draft a route of your own."
        )}
      </p>
    </section>
  );
}
