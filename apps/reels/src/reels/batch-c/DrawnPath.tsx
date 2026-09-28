import type { SVGProps } from "react";

type DrawnPathProps = Omit<SVGProps<SVGPathElement>, "pathLength"> & { drawn: number };

/** A path that draws itself on like a pen stroke: drawn runs from 0 (nothing) to 1 (the whole path). */
export function DrawnPath({ drawn, ...path }: DrawnPathProps) {
  return <path fill="none" strokeLinecap="round" strokeLinejoin="round" {...path} pathLength={1} strokeDasharray={1} strokeDashoffset={1 - Math.min(1, Math.max(0, drawn))} />;
}
