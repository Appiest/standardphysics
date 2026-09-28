import type { ReactNode } from "react";
import { useCurrentFrame } from "remotion";
import { progress } from "../lib/ease";

export function FinePrint({ children, at, className }: { children: ReactNode; at: number; className?: string }) {
  const frame = useCurrentFrame();
  return (
    <p className={`font-body text-fineprint text-ink-muted ${className ?? ""}`} style={{ opacity: progress(frame, at, 14) }}>
      {children}
    </p>
  );
}
