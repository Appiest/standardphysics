"use client";

import { Html, Line } from "@react-three/drei";
import type { ThreeEvent } from "@react-three/fiber";
import { useRef } from "react";
import { Plane, Vector3 } from "three";
import type { StopMarker } from "@/lib/route";
import { MODEL } from "./palette";

const FLOOR = new Plane(new Vector3(0, 1, 0), 0);
const LABEL_STEP_PERCENT = 115;

export type RouteHandles = {
  markers: StopMarker[];
  editable: boolean;
  onGrab: () => void;
  onDrag: (marker: StopMarker, dx: number, dy: number) => void;
  onDrop: () => void;
  /** The route a customer walks between stops, one line per leg, around what's in the way. */
  legs?: { x: number; y: number }[][];
};

function useMarkerDrag(marker: StopMarker, route: RouteHandles) {
  const from = useRef<Vector3 | null>(null);
  const hit = (event: ThreeEvent<PointerEvent>) => event.ray.intersectPlane(FLOOR, new Vector3());
  if (!route.editable) return {};
  return {
    onPointerDown(event: ThreeEvent<PointerEvent>) {
      event.stopPropagation();
      (event.target as unknown as Element).setPointerCapture(event.pointerId);
      from.current = hit(event);
      route.onGrab();
    },
    onPointerMove(event: ThreeEvent<PointerEvent>) {
      const point = from.current && hit(event);
      if (!from.current || !point) return;
      route.onDrag(marker, point.x - from.current.x, -(point.z - from.current.z));
      from.current = point;
    },
    onPointerUp(event: ThreeEvent<PointerEvent>) {
      if (!from.current) return;
      (event.target as unknown as Element).releasePointerCapture(event.pointerId);
      from.current = null;
      route.onDrop();
    },
  };
}

/**
 * Crowded labels take turns below and above their marker. Lifting every one
 * upward put a label straight onto its neighbour's whenever that neighbour sat
 * just above it on screen, which from overhead is most of the time.
 */
function labelOffset(tier: number): number {
  if (tier === 0) return 0;
  const direction = tier % 2 === 1 ? 1 : -1;
  return direction * Math.ceil(tier / 2) * LABEL_STEP_PERCENT;
}

function Marker({ marker, route }: { marker: StopMarker; route: RouteHandles }) {
  const drag = useMarkerDrag(marker, route);
  return (
    <group position={[marker.x, 0, -marker.y]}>
      <mesh position-y={0.02} rotation-x={-Math.PI / 2} {...drag}>
        <circleGeometry args={[0.28, 32]} />
        <meshBasicMaterial color={MODEL.accent} transparent opacity={route.editable ? 0.85 : 0.5} depthWrite={false} />
      </mesh>
      <Html position={[0, 0.3, 0]} center style={{ pointerEvents: "none" }}>
        <span
          className="block whitespace-nowrap rounded-md bg-accent px-2 py-1 text-sm font-semibold text-paper shadow-md"
          style={{ transform: `translateY(${labelOffset(marker.labelTier)}%)` }}
        >
          {marker.label}
        </span>
      </Html>
    </group>
  );
}

function WalkingLeg({ path }: { path: { x: number; y: number }[] }) {
  if (path.length < 2) return null;
  const points = path.map(({ x, y }) => [x, 0.03, -y] as [number, number, number]);
  return <Line points={points} color={MODEL.accent} lineWidth={3} dashed dashSize={0.25} gapSize={0.18} depthTest={false} renderOrder={2} />;
}

export function StopMarkers({ route }: { route: RouteHandles }) {
  return (
    <group>
      {route.legs?.map((leg, index) => <WalkingLeg key={index} path={leg} />)}
      {route.markers.map((marker) => (
        <Marker key={marker.key} marker={marker} route={route} />
      ))}
    </group>
  );
}
