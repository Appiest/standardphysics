"use client";

import { TitleBlock } from "./TitleBlock";
import { useSchematicBrush } from "./useSchematicBrush";
import { SheetFrame } from "./SheetFrame";

export function DraftingSheet() {
  const { surfaceRef, paintRef, liveRef, zoneGrid, penZone, hasDrawn, canvasHandlers, clearSheet, replayDemo, saveImage } = useSchematicBrush();

  return (
    <main className="flex h-dvh bg-paper px-4 py-4 sm:px-6 sm:py-6">
      <SheetFrame columns={zoneGrid.columns} rows={zoneGrid.rows} className="flex-1 bg-sheet">
        <div ref={surfaceRef} className="drafting-grid drafting-sheet absolute inset-0 overflow-hidden">
          <canvas ref={paintRef} aria-hidden className="absolute inset-0 size-full" />
          <canvas
            ref={liveRef}
            role="img"
            aria-label="Drafting sheet. Drag to draw a schematic route with callouts, dimensions, and ADA code notes."
            className="absolute inset-0 size-full cursor-crosshair touch-none"
            {...canvasHandlers}
          />
          <div aria-hidden className="paper-grain pointer-events-none absolute inset-0" />
          <TitleBlock penZone={penZone} hasDrawn={hasDrawn} onClear={clearSheet} onReplay={replayDemo} onSave={saveImage} />
        </div>
      </SheetFrame>
    </main>
  );
}
