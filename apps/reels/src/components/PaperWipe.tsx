import type { ReactNode } from "react";
import { useCurrentFrame } from "remotion";
import { progress, sweep } from "../lib/ease";
import { Paper } from "./Paper";
import { ScanBar } from "./ScanBar";

/** Brings the paper back up over whatever was on screen, led by the scan bar. */
export function PaperWipe({ duration = 18, children }: { duration?: number; children?: ReactNode }) {
  const frame = useCurrentFrame();
  const covered = progress(frame, 0, duration, sweep);
  const edge = 1 - covered;
  return (
    <>
      <div className="absolute inset-0" style={{ clipPath: `inset(${edge * 100}% 0 0 0)` }}>
        <Paper>{children}</Paper>
      </div>
      {covered < 1 && <ScanBar at={edge} direction={-1} trail={0.1} />}
    </>
  );
}
