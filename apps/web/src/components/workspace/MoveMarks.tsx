"use client";

import { Line } from "@react-three/drei";
import { useEffect, useMemo } from "react";
import { CanvasTexture, RepeatWrapping, SRGBColorSpace, Vector3, type Texture } from "three";
import type { SceneNode } from "@/types/contracts";
import { MODEL } from "./palette";

const HATCH_SPACING = 0.08;
const SHADOW_SPREAD = 1.2;
/** Marks sit a hair above the floor and are pulled toward the camera, so the scanned floor never shows through them. */
const LIFT = 0.006;
const PULL_FORWARD = { polygonOffset: true, polygonOffsetFactor: -4, polygonOffsetUnits: -4 } as const;
/** Lines are drawn over the scan: a bumpy scanned floor would otherwise swallow a line lying on it. */
const ON_TOP = { depthTest: false, renderOrder: 3, lineWidth: 3, color: MODEL.accent } as const;

let hatchImage: HTMLCanvasElement | null = null;
let shadowImage: HTMLCanvasElement | null = null;

/** One tile of diagonal stripes: the unmeasured grey laid over a lighter floor, repeated across the vacated spot. */
function hatchTile(): HTMLCanvasElement {
  if (hatchImage) return hatchImage;
  const canvas = Object.assign(document.createElement("canvas"), { width: 32, height: 32 });
  const pen = canvas.getContext("2d")!;
  pen.fillStyle = MODEL.floor;
  pen.fillRect(0, 0, 32, 32);
  pen.strokeStyle = "#6f6a62";
  pen.lineWidth = 9;
  for (const offset of [-32, 0, 32]) {
    pen.beginPath();
    pen.moveTo(offset, 32);
    pen.lineTo(offset + 32, 0);
    pen.stroke();
  }
  hatchImage = canvas;
  return canvas;
}

/** A soft dark pool that fades to nothing at its edge, the shade a piece throws on the floor under it. */
function shadowTile(): HTMLCanvasElement {
  if (shadowImage) return shadowImage;
  const canvas = Object.assign(document.createElement("canvas"), { width: 64, height: 64 });
  const pen = canvas.getContext("2d")!;
  const pool = pen.createRadialGradient(32, 32, 6, 32, 32, 32);
  pool.addColorStop(0, "rgba(20, 18, 15, 0.7)");
  pool.addColorStop(1, "rgba(20, 18, 15, 0)");
  pen.fillStyle = pool;
  pen.fillRect(0, 0, 64, 64);
  shadowImage = canvas;
  return canvas;
}

function useTexture(image: () => HTMLCanvasElement, repeatX = 1, repeatY = 1): Texture {
  const texture = useMemo(() => {
    const made = new CanvasTexture(image());
    made.colorSpace = SRGBColorSpace;
    made.wrapS = made.wrapT = RepeatWrapping;
    made.repeat.set(repeatX, repeatY);
    return made;
  }, [image, repeatX, repeatY]);
  useEffect(() => () => texture.dispose(), [texture]);
  return texture;
}

/** A point on the floor under the node, in viewer space: scene (x, y, z) becomes (x, z, -y). */
function floorPoint(node: SceneNode, x = 0, y = 0): Vector3 {
  const m = node.transform.m;
  const [atX, atY] = [m[3] + m[0] * x + m[1] * y, m[7] + m[4] * x + m[5] * y];
  return new Vector3(atX, m[11] - node.dimensions.z / 2 + LIFT, -atY);
}

function yawOf(node: SceneNode): number {
  return Math.atan2(node.transform.m[4], node.transform.m[0]);
}

/** Where the piece stood: hatched like every surface the phone never saw, and outlined so the spot reads at a glance. */
function VacatedSpot({ node }: { node: SceneNode }) {
  const { x, y } = node.dimensions;
  const hatch = useTexture(hatchTile, x / HATCH_SPACING / 4, y / HATCH_SPACING / 4);
  const [hx, hy] = [x / 2, y / 2];
  const outline = [floorPoint(node, -hx, -hy), floorPoint(node, hx, -hy), floorPoint(node, hx, hy), floorPoint(node, -hx, hy), floorPoint(node, -hx, -hy)];
  return (
    <>
      <mesh position={floorPoint(node)} rotation={[-Math.PI / 2, 0, yawOf(node)]} raycast={() => null}>
        <planeGeometry args={[x, y]} />
        <meshBasicMaterial map={hatch} {...PULL_FORWARD} />
      </mesh>
      <Line points={outline} {...ON_TOP} dashed dashSize={0.08} gapSize={0.05} />
    </>
  );
}

/** Where the piece landed: a soft shadow grounds it, so it reads as standing there rather than pasted on. */
function Landing({ node }: { node: SceneNode }) {
  const shadow = useTexture(shadowTile);
  return (
    <mesh position={floorPoint(node)} rotation={[-Math.PI / 2, 0, yawOf(node)]} raycast={() => null} renderOrder={1}>
      <planeGeometry args={[node.dimensions.x * SHADOW_SPREAD, node.dimensions.y * SHADOW_SPREAD]} />
      <meshBasicMaterial map={shadow} transparent depthWrite={false} {...PULL_FORWARD} />
    </mesh>
  );
}

/** What a move did, drawn on the floor: the spot it left, the path it took and where it stands now. */
export function MoveMarks({ from, to }: { from: SceneNode; to: SceneNode }) {
  return (
    <group>
      <VacatedSpot node={from} />
      <Landing node={to} />
      <Line points={[floorPoint(from), floorPoint(to)]} {...ON_TOP} dashed dashSize={0.12} gapSize={0.08} />
    </group>
  );
}
