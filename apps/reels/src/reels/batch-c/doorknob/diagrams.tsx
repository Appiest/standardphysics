import type { ReactNode } from "react";
import { DrawnPath } from "../DrawnPath";

/** Every diagram shares one drawing space and one pen, so the six cards read as one set of plates. */
export const PLATE = { width: 900, height: 760 } as const;

const ink = { stroke: "#0d0d0c", strokeWidth: 9 } as const;
const thin = { stroke: "#0d0d0c", strokeWidth: 5 } as const;
const limit = { stroke: "#f6be1a", strokeWidth: 8, strokeDasharray: "22 16" } as const;
const fail = { stroke: "#d9291f", strokeWidth: 12 } as const;

type Plate = (drawn: number) => ReactNode;

const stage = (drawn: number, from: number, to: number) => Math.min(1, Math.max(0, (drawn - from) / (to - from)));

function Label({ x, y, children, anchor = "middle" }: { x: number; y: number; children: ReactNode; anchor?: "start" | "middle" | "end" }) {
  return (
    <text x={x} y={y} textAnchor={anchor} className="reel-copy figures fill-ink" fontSize={64}>
      {children}
    </text>
  );
}

const knob: Plate = (drawn) => (
  <g>
    <DrawnPath d="M 250 40 L 250 720" {...ink} drawn={stage(drawn, 0, 0.3)} />
    <DrawnPath d="M 450 250 m -150 0 a 150 150 0 1 0 300 0 a 150 150 0 1 0 -300 0" {...ink} drawn={stage(drawn, 0.1, 0.45)} />
    <DrawnPath d="M 450 250 m -88 0 a 88 88 0 1 0 176 0 a 88 88 0 1 0 -176 0" {...ink} drawn={stage(drawn, 0.25, 0.55)} />
    <DrawnPath d="M 640 150 A 220 220 0 0 1 640 350" {...thin} drawn={stage(drawn, 0.45, 0.7)} />
    <DrawnPath d="M 612 330 L 640 352 L 668 322" {...thin} drawn={stage(drawn, 0.62, 0.72)} />
    <text x={700} y={270} className="reel-caption fill-ink" fontSize={64} opacity={stage(drawn, 0.6, 0.75)}>
      twist
    </text>
    <DrawnPath d="M 280 480 L 640 30" {...fail} drawn={stage(drawn, 0.8, 1)} />
  </g>
);

const counter: Plate = (drawn) => (
  <g>
    <DrawnPath d="M 40 700 L 860 700" {...ink} drawn={stage(drawn, 0, 0.2)} />
    <DrawnPath d="M 180 700 L 180 250 L 760 250 L 760 700" {...ink} drawn={stage(drawn, 0.1, 0.45)} />
    <DrawnPath d="M 60 340 L 840 340" {...limit} drawn={stage(drawn, 0.45, 0.7)} />
    <Label x={470} y={320}>36 in max</Label>
    <DrawnPath d="M 820 250 L 820 340" {...fail} drawn={stage(drawn, 0.7, 0.9)} />
  </g>
);

const doorway: Plate = (drawn) => (
  <g>
    <DrawnPath d="M 40 700 L 860 700" {...ink} drawn={stage(drawn, 0, 0.2)} />
    <DrawnPath d="M 220 700 L 220 90 L 680 90 L 680 700" {...ink} drawn={stage(drawn, 0.1, 0.45)} />
    <DrawnPath d="M 250 700 L 250 130 L 330 60" {...thin} drawn={stage(drawn, 0.35, 0.55)} />
    <DrawnPath d="M 260 470 L 670 470" {...limit} drawn={stage(drawn, 0.5, 0.75)} />
    <DrawnPath d="M 290 440 L 260 470 L 290 500 M 640 440 L 670 470 L 640 500" {...thin} drawn={stage(drawn, 0.65, 0.8)} />
    <Label x={460} y={430}>32 in min</Label>
  </g>
);

const ramp: Plate = (drawn) => (
  <g>
    <DrawnPath d="M 40 700 L 860 700" {...ink} drawn={stage(drawn, 0, 0.2)} />
    <DrawnPath d="M 100 700 L 800 460 L 800 700" {...ink} drawn={stage(drawn, 0.1, 0.5)} />
    <DrawnPath d="M 100 700 L 800 642" {...limit} drawn={stage(drawn, 0.5, 0.75)} />
    <Label x={120} y={420} anchor="start">1 in rise</Label>
    <Label x={120} y={495} anchor="start">per 12 in</Label>
    <DrawnPath d="M 820 460 L 820 640" {...fail} drawn={stage(drawn, 0.75, 0.95)} />
  </g>
);

const mirror: Plate = (drawn) => (
  <g>
    <DrawnPath d="M 40 720 L 860 720" {...ink} drawn={stage(drawn, 0, 0.2)} />
    <DrawnPath d="M 250 450 L 650 450 L 620 520 L 280 520 Z" {...ink} drawn={stage(drawn, 0.1, 0.4)} />
    <DrawnPath d="M 300 60 L 600 60 L 600 330 L 300 330 Z" {...ink} drawn={stage(drawn, 0.3, 0.6)} />
    <DrawnPath d="M 150 400 L 750 400" {...limit} drawn={stage(drawn, 0.55, 0.8)} />
    <Label x={150} y={385} anchor="start">40 in max</Label>
    <DrawnPath d="M 640 330 L 640 400" {...fail} drawn={stage(drawn, 0.8, 0.95)} />
  </g>
);

const threshold: Plate = (drawn) => (
  <g>
    <DrawnPath d="M 40 600 L 330 600 L 380 520 L 520 520 L 570 600 L 860 600" {...ink} drawn={stage(drawn, 0, 0.45)} />
    <DrawnPath d="M 250 560 L 650 560" {...limit} drawn={stage(drawn, 0.45, 0.7)} />
    <Label x={450} y={470}>½ in max</Label>
    <DrawnPath d="M 690 520 L 690 560" {...fail} drawn={stage(drawn, 0.7, 0.9)} />
    <DrawnPath d="M 660 520 L 720 520 M 660 600 L 720 600" {...thin} drawn={stage(drawn, 0.7, 0.9)} />
  </g>
);

export const plates = { knob, counter, doorway, ramp, mirror, threshold } as const;
export type PlateName = keyof typeof plates;
