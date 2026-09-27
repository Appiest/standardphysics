import { drawnNodes, footprint } from "@/components/FloorPlan";
import type { Finding, SceneGraph, SceneNode, Vec3 } from "@/types/contracts";

const WINDOW_METERS = 4;
const KIND_ORDER = ["floor", "object", "wall"];

/**
 * The spot a finding is about, drawn from above: the pieces around it in ink,
 * the pieces it's about in red, and the measurement as a red dimension line.
 * Made from the measured model rather than a render, so it shows the problem
 * instead of a patch of floor.
 */
export function SpotPlan({ scene, finding }: { scene: SceneGraph; finding: Finding }) {
  const locus = finding.locus;
  if (!locus) return null;
  const involved = new Set(locus.node_ids);
  const size = Math.max(WINDOW_METERS, spread(locus.bbox_min, locus.bbox_max) * 1.6);
  const view = `${locus.point.x - size / 2} ${-locus.point.y - size / 2} ${size} ${size}`;
  const nodes = [...drawnNodes(scene)].sort((a, b) => KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind));
  return (
    <svg viewBox={view} className="aspect-[2/1] w-full rounded-xl bg-sheet outline outline-1 -outline-offset-1 outline-black/10" preserveAspectRatio="xMidYMid slice" aria-hidden>
      {nodes.map((node) => <Piece key={node.id} node={node} involved={involved.has(node.id)} />)}
      <Measurement points={locus.annotation.points} />
    </svg>
  );
}

function spread(min: Vec3, max: Vec3): number {
  return Math.max(Math.abs(max.x - min.x), Math.abs(max.y - min.y));
}

const PIECE_STYLE: Record<string, { fill: string; stroke: string }> = {
  floor: { fill: "none", stroke: "var(--color-rule)" },
  wall: { fill: "var(--color-ink)", stroke: "var(--color-ink)" },
  object: { fill: "var(--color-ink)", stroke: "var(--color-ink)" },
};

function Piece({ node, involved }: { node: SceneNode; involved: boolean }) {
  const { x, y, width, depth, degrees } = footprint(node);
  const base = PIECE_STYLE[node.kind] ?? PIECE_STYLE.object;
  const fill = involved ? "var(--color-problem)" : base.fill;
  const opacity = node.kind === "object" && !involved ? 0.14 : involved ? 0.22 : 1;
  return (
    <rect
      x={-width / 2} y={-depth / 2} width={width} height={depth}
      fill={fill} fillOpacity={opacity}
      stroke={involved ? "var(--color-problem)" : base.stroke} strokeWidth={involved ? 2 : 1}
      vectorEffect="non-scaling-stroke"
      transform={`translate(${x.toFixed(4)} ${(-y).toFixed(4)}) rotate(${(-degrees).toFixed(3)})`}
    />
  );
}

function Measurement({ points }: { points: Vec3[] }) {
  if (points.length < 2) return null;
  const path = points.map((point) => `${point.x.toFixed(4)},${(-point.y).toFixed(4)}`).join(" ");
  return <polyline points={path} fill="none" stroke="var(--color-problem)" strokeWidth={3} strokeLinecap="round" vectorEffect="non-scaling-stroke" />;
}
