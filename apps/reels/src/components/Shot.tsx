import type { ReactNode } from "react";
import { useCurrentFrame } from "remotion";
import { Footage } from "./Footage";
import { drawn, progress } from "../lib/ease";

/** Every cut lands with a punch: the frame starts a little close and a white flash burns off. */
export function Punch({ children, push = 0.0008 }: { children: ReactNode; push?: number }) {
  const frame = useCurrentFrame();
  const settle = progress(frame, 0, 9, drawn);
  const scale = 1.1 - 0.1 * settle + frame * push;
  return (
    <div className="absolute inset-0 overflow-hidden">
      <div className="absolute inset-0" style={{ transform: `scale(${scale})` }}>
        {children}
      </div>
      <div className="absolute inset-0 bg-paper-raised" style={{ opacity: 0.85 * (1 - progress(frame, 0, 5)) }} />
    </div>
  );
}

export function Shot({ clip, startFrom = 0, children }: { clip: string; startFrom?: number; children?: ReactNode }) {
  return (
    <Punch>
      <Footage clip={clip} startFrom={startFrom} />
      {children}
    </Punch>
  );
}
