import type { CSSProperties } from "react";
import { boxOf, onSheet, type SheetFrame } from "../../lib/plan";
import type { ScanObject } from "../../lib/scan";

type Face = { key: string; style: CSSProperties; shade: string };

const edge = "inset 0 0 0 2px #0d0d0c";

function facesOf(width: number, depth: number, height: number): Face[] {
  return [
    { key: "top", shade: "#f3f3f1", style: { width, height: depth, transform: `translateZ(${height}px)` } },
    { key: "front", shade: "#d2d2cd", style: { width, height, transform: `translateY(${depth}px) rotateX(90deg)`, transformOrigin: "0 0" } },
    { key: "back", shade: "#c2c2bc", style: { width, height, transform: "rotateX(90deg)", transformOrigin: "0 0" } },
    { key: "left", shade: "#b8b8b1", style: { width: height, height: depth, transform: "rotateY(-90deg)", transformOrigin: "0 0" } },
    { key: "right", shade: "#dcdcd7", style: { width: height, height: depth, transform: `translateX(${width}px) rotateY(-90deg)`, transformOrigin: "0 0" } },
  ];
}

function PaperBox({ object, frame, rise }: { object: ScanObject; frame: SheetFrame; rise: number }) {
  const box = boxOf(object);
  const [x, y] = onSheet(frame, box.center);
  const width = box.width * frame.scale;
  const depth = box.depth * frame.scale;
  const height = Math.max(0.5, box.height * frame.scale * rise);
  return (
    <div
      className="absolute"
      style={{ left: x, top: y, width, height: depth, transformStyle: "preserve-3d", transform: `translate(-50%, -50%) rotateZ(${box.angle}rad)` }}
    >
      <div className="absolute inset-0 bg-ink/25 blur-md" style={{ transform: `translate(${height * 0.35}px, ${height * 0.2}px)`, opacity: rise }} />
      {facesOf(width, depth, height).map((face) => (
        <div key={face.key} className="absolute left-0 top-0" style={{ ...face.style, background: face.shade, boxShadow: edge, opacity: Math.min(1, rise * 4) }} />
      ))}
    </div>
  );
}

/** Every object RoomPlan found, stood up as a paper box at its measured footprint and height. */
export function PaperModel({ objects, frame, riseOf }: { objects: ScanObject[]; frame: SheetFrame; riseOf: (index: number) => number }) {
  return (
    <div className="absolute inset-0" style={{ transformStyle: "preserve-3d" }}>
      {objects.map((object, index) => (
        <PaperBox key={index} object={object} frame={frame} rise={riseOf(index)} />
      ))}
    </div>
  );
}
