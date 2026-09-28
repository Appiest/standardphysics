import { TapeLabel } from "./Type";

/** Captions for footage: short lines of masking tape stacked at the top of the frame, slapped on one after another. */
export function TapeStack({ lines, at = 6, exitAt }: { lines: string[]; at?: number; exitAt?: number }) {
  return (
    <div className="absolute inset-x-safe-side top-safe-top flex flex-col items-start gap-4">
      {lines.map((line, index) => (
        <TapeLabel key={line} at={at + index * 5} exitAt={exitAt === undefined ? undefined : exitAt + index * 2} tilt={index % 2 === 0 ? -2 : 1.5} className="reel-caption text-caption">
          {line}
        </TapeLabel>
      ))}
    </div>
  );
}
