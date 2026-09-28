"use client";

import { Edges, Html } from "@react-three/drei";
import { type ThreeEvent, useThree } from "@react-three/fiber";
import { memo, useEffect, useMemo } from "react";
import { BoxGeometry, type Matrix4, Vector3 } from "three";
import { formatInches } from "@/lib/findings";
import { boxTop, type FoundMark, type PieceSize } from "@/lib/found-objects";
import type { SceneGraph, SceneNode } from "@/types/contracts";
import { MODEL } from "./palette";
import { boxMatrix } from "./ShopModel";

/** What the owner's list of found pieces hands the 3D view, and how the view answers back. */
export type FoundHandles = {
  marks: FoundMark[];
  hoveredRowId: string | null;
  selectedRowId: string | null;
  selectedNodeId: string | null;
  hoveredNodeId: string | null;
  onHoverNode: (nodeId: string | null) => void;
  onPickNode: (nodeId: string) => void;
};

type MarkState = "idle" | "hovered" | "selected";

const UNIT_BOX = new BoxGeometry(1, 1, 1);
const LABEL_LIFT_METERS = 0.12;
/** Past this many pieces a row's labels would bury the scan, so only the outlines light up. */
const MOST_LABELS_PER_ROW = 3;

/** How a piece's box reads in each state: faint and hidden behind surfaces at rest, accent and in front of everything when pointed at. */
const LOOKS: Record<MarkState, { fillOpacity: number | null; edgeColor: string; edgeOpacity: number; lineWidth: number; inFront: boolean }> = {
  idle: { fillOpacity: null, edgeColor: MODEL.ink, edgeOpacity: 0.35, lineWidth: 1, inFront: false },
  hovered: { fillOpacity: 0.1, edgeColor: MODEL.accent, edgeOpacity: 1, lineWidth: 2.5, inFront: true },
  selected: { fillOpacity: 0.16, edgeColor: MODEL.accent, edgeOpacity: 1, lineWidth: 2.5, inFront: true },
};

/** A picked piece stands out from the rest of its row, which stays lit around it. */
function stateOf(mark: FoundMark, handles: FoundHandles): MarkState {
  if (handles.selectedNodeId) return pieceState(mark, handles);
  if (mark.rowId === handles.selectedRowId) return "selected";
  if (mark.rowId === handles.hoveredRowId) return "hovered";
  return "idle";
}

function pieceState(mark: FoundMark, handles: FoundHandles): MarkState {
  if (mark.nodeId === handles.selectedNodeId) return "selected";
  return mark.rowId === handles.selectedRowId || mark.rowId === handles.hoveredRowId ? "hovered" : "idle";
}

function rowSizes(marks: FoundMark[]): Map<string, number> {
  const sizes = new Map<string, number>();
  for (const mark of marks) sizes.set(mark.rowId, (sizes.get(mark.rowId) ?? 0) + 1);
  return sizes;
}

/**
 * A label names the piece under the pointer, and every piece of a small row the
 * owner points at. Labels at rest were tried and piled up over the counter,
 * where the pieces worth naming sit a hand apart.
 */
function isLabelled(state: MarkState, rowSize: number, pointedAt: boolean): boolean {
  return pointedAt || (state !== "idle" && rowSize <= MOST_LABELS_PER_ROW);
}

function labelAnchor(node: SceneNode): Vector3 {
  const m = node.transform.m;
  return new Vector3(m[3], boxTop(node) + LABEL_LIFT_METERS, -m[7]);
}

function SizeLine({ size }: { size: PieceSize }) {
  const parts: [number, string][] = [[size.wideInches, "wide"], [size.deepInches, "deep"], [size.tallInches, "tall"]];
  return (
    <span className="flex gap-2.5 text-paper/70">
      {parts.map(([inches, word]) => (
        <span key={word}><span className="measurement text-paper">{formatInches(inches)}</span> {word}</span>
      ))}
    </span>
  );
}

/** A piece's name and top height; the one the owner picked also carries its size. */
function MarkLabel({ mark, node, sized }: { mark: FoundMark; node: SceneNode; sized: boolean }) {
  const anchor = useMemo(() => labelAnchor(node), [node]);
  return (
    <Html position={anchor} center zIndexRange={[20, 0]} style={{ pointerEvents: "none" }}>
      <span className={`flex flex-col gap-0.5 whitespace-nowrap bg-ink text-sm text-paper shadow-float ${sized ? "rounded-xl px-3 py-2" : "rounded-full px-2.5 py-1"}`}>
        <span className="flex items-baseline gap-1.5">
          <span className="font-medium">{mark.name}</span>
          {mark.topInches !== null && <span className="text-paper/70">top <span className="measurement text-paper">{formatInches(mark.topInches)}</span></span>}
        </span>
        {sized && <SizeLine size={mark.size} />}
      </span>
    </Html>
  );
}

function setCursor(cursor: string) {
  document.body.style.cursor = cursor;
}

function BoxFill({ opacity }: { opacity: number | null }) {
  if (opacity === null) return <meshBasicMaterial colorWrite={false} depthWrite={false} />;
  return <meshBasicMaterial color={MODEL.accent} transparent opacity={opacity} depthWrite={false} depthTest={false} />;
}

const FoundBox = memo(function FoundBox({ mark, node, matrix, state, labelled, onHoverNode, onPickNode }: {
  mark: FoundMark;
  node: SceneNode;
  matrix: Matrix4;
  state: MarkState;
  labelled: boolean;
  onHoverNode: (nodeId: string | null) => void;
  onPickNode: (nodeId: string) => void;
}) {
  const look = LOOKS[state];
  const layer = look.inFront ? 6 : 4;
  const pointer = {
    onPointerOver: (event: ThreeEvent<PointerEvent>) => { event.stopPropagation(); setCursor("pointer"); onHoverNode(mark.nodeId); },
    onPointerOut: () => { setCursor("auto"); onHoverNode(null); },
    onClick: (event: ThreeEvent<MouseEvent>) => { event.stopPropagation(); onPickNode(mark.nodeId); },
  };
  return (
    <>
      <mesh geometry={UNIT_BOX} matrix={matrix} matrixAutoUpdate={false} renderOrder={layer} {...pointer}>
        <BoxFill opacity={look.fillOpacity} />
        <Edges threshold={20} lineWidth={look.lineWidth} color={look.edgeColor} transparent opacity={look.edgeOpacity} depthTest={!look.inFront} renderOrder={layer + 1} />
      </mesh>
      {labelled && <MarkLabel mark={mark} node={node} sized={state === "selected"} />}
    </>
  );
});

function Outlines({ scene, handles }: { scene: SceneGraph; handles: FoundHandles }) {
  const invalidate = useThree((state) => state.invalidate);
  const placed = useMemo(() => {
    const byId = new Map(scene.nodes.map((node) => [node.id, node]));
    return handles.marks.flatMap((mark) => {
      const node = byId.get(mark.nodeId);
      return node ? [{ mark, node, matrix: boxMatrix(node) }] : [];
    });
  }, [scene, handles.marks]);
  const sizes = useMemo(() => rowSizes(handles.marks), [handles.marks]);
  useEffect(() => invalidate(), [handles.hoveredRowId, handles.selectedRowId, handles.hoveredNodeId, placed, invalidate]);
  useEffect(() => () => setCursor("auto"), []);
  return (
    <group>
      {placed.map(({ mark, node, matrix }) => {
        const state = stateOf(mark, handles);
        const pointedAt = handles.hoveredNodeId === mark.nodeId || handles.selectedNodeId === mark.nodeId;
        const labelled = isLabelled(state, sizes.get(mark.rowId) ?? 0, pointedAt);
        return <FoundBox key={mark.nodeId} mark={mark} node={node} matrix={matrix} state={state} labelled={labelled} onHoverNode={handles.onHoverNode} onPickNode={handles.onPickNode} />;
      })}
    </group>
  );
}

/**
 * The pieces the scan found, drawn as thin outlines over the painted scan.
 * The row the owner points at, in the list or here, lights up; nothing is
 * drawn on the steps that don't list the pieces.
 */
export function FoundOutlines({ scene, handles }: { scene: SceneGraph; handles?: FoundHandles | null }) {
  if (!handles) return null;
  return <Outlines scene={scene} handles={handles} />;
}
