import { useCurrentFrame } from "remotion";
import { drawn, progress } from "../lib/ease";

export type BehindTypeProps = { lines: string[]; top: number; at?: number; tone?: "light" | "ink" };

const behindTone = {
  light: { className: "text-paper-raised", textShadow: "0 12px 60px rgba(13,13,12,0.35)" },
  ink: { className: "text-ink", textShadow: "0 0 40px rgba(243,243,241,0.55)" },
} as const;

/** Huge type meant to sit between footage and a person cut out of it, so they stand in front of the words. */
export function BehindType({ lines, top, at = 0, tone = "light" }: BehindTypeProps) {
  const frame = useCurrentFrame();
  return (
    <div className="absolute inset-x-0 text-center" style={{ top }}>
      {lines.map((line, index) => {
        const rise = progress(frame, at + index * 4, 16, drawn);
        return (
          <div key={line} className="overflow-hidden">
            <div className={`reel-copy text-poster ${behindTone[tone].className}`} style={{ transform: `translateY(${(1 - rise) * 105}%)`, textShadow: behindTone[tone].textShadow }}>
              {line}
            </div>
          </div>
        );
      })}
    </div>
  );
}
