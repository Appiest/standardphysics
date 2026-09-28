"use client";

import { Html, Line } from "@react-three/drei";
import type { ThreeEvent } from "@react-three/fiber";
import { useMemo, useRef, useState } from "react";
import { Color, Plane, Vector3 } from "three";
import { CORNERS, type Corner, cornerPoint, outline } from "@/lib/staff-areas";
import type { StaffArea } from "@/types/contracts";
import { MODEL } from "./palette";

const FLOOR = new Plane(new Vector3(0, 1, 0), 0);
const LIFT = 0.015;
const HANDLE_RADIUS = 0.16;

export type StaffHandles = {
  areas: StaffArea[];
  editable: boolean;
  onGrab: () => void;
  onMove: (index: number, dx: number, dy: number) => void;
  onResize: (index: number, corner: Corner, to: { x: number; y: number }) => void;
  onDrop: () => void;
};

type FloorPoint = { x: number; y: number };
type Drag = (from: FloorPoint, to: FloorPoint) => void;

/** Pointer handlers that report floor points in room coordinates, where y is -z on screen. */
function useFloorDrag(handles: StaffHandles, onDrag: Drag) {
  const last = useRef<FloorPoint | null>(null);
  const hit = (event: ThreeEvent<PointerEvent>) => {
    const point = event.ray.intersectPlane(FLOOR, new Vector3());
    return point && { x: point.x, y: -point.z };
  };
  if (!handles.editable) return {};
  return {
    onPointerDown(event: ThreeEvent<PointerEvent>) {
      event.stopPropagation();
      (event.target as unknown as Element).setPointerCapture(event.pointerId);
      last.current = hit(event);
      handles.onGrab();
    },
    onPointerMove(event: ThreeEvent<PointerEvent>) {
      const point = last.current && hit(event);
      if (!last.current || !point) return;
      onDrag(last.current, point);
      last.current = point;
    },
    onPointerUp(event: ThreeEvent<PointerEvent>) {
      if (!last.current) return;
      (event.target as unknown as Element).releasePointerCapture(event.pointerId);
      last.current = null;
      handles.onDrop();
    },
  };
}

const vertexShader = /* glsl */ `
  varying vec3 vWorld;
  void main() {
    vec4 world = modelMatrix * vec4(position, 1.0);
    vWorld = world.xyz;
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`;

const fragmentShader = /* glsl */ `
  uniform vec3 uColor;
  varying vec3 vWorld;
  void main() {
    float stripe = step(0.6, fract((vWorld.x + vWorld.z) * 5.0));
    gl_FragColor = vec4(uColor, mix(0.06, 0.3, stripe));
    #include <colorspace_fragment>
  }
`;

function CornerHandle({ area, corner, index, handles }: { area: StaffArea; corner: Corner; index: number; handles: StaffHandles }) {
  const drag = useFloorDrag(handles, (_from, to) => handles.onResize(index, corner, to));
  const at = cornerPoint(area, corner);
  return (
    <mesh position={[at.x, LIFT * 2, -at.y]} rotation-x={-Math.PI / 2} renderOrder={3} {...drag}>
      <circleGeometry args={[HANDLE_RADIUS, 24]} />
      <meshBasicMaterial color={MODEL.ink} depthWrite={false} depthTest={false} />
    </mesh>
  );
}

function Area({ area, index, handles }: { area: StaffArea; index: number; handles: StaffHandles }) {
  const drag = useFloorDrag(handles, (from, to) => handles.onMove(index, to.x - from.x, to.y - from.y));
  const [uniforms] = useState(() => ({ uColor: { value: new Color(MODEL.ink) } }));
  const edge = useMemo(() => {
    const corners = outline(area).map(({ x, y }) => [x, LIFT, -y] as [number, number, number]);
    return [...corners, corners[0]];
  }, [area]);
  return (
    <group>
      <mesh
        position={[area.centre.x, LIFT, -area.centre.y]}
        rotation={[-Math.PI / 2, 0, (area.rotation_z_degrees * Math.PI) / 180]}
        renderOrder={1}
        {...drag}
      >
        <planeGeometry args={[area.width, area.depth]} />
        <shaderMaterial vertexShader={vertexShader} fragmentShader={fragmentShader} uniforms={uniforms} transparent depthWrite={false} />
      </mesh>
      <Line points={edge} color={MODEL.ink} lineWidth={2} dashed dashSize={0.2} gapSize={0.12} depthTest={false} renderOrder={2} />
      <Html position={[area.centre.x, 0.3, -area.centre.y]} center style={{ pointerEvents: "none" }}>
        <span className="block whitespace-nowrap rounded-md bg-ink px-2 py-1 text-sm font-semibold text-paper shadow-md">Staff only</span>
      </Html>
      {handles.editable && CORNERS.map((corner) => (
        <CornerHandle key={`${corner.alongSign}${corner.acrossSign}`} area={area} corner={corner} index={index} handles={handles} />
      ))}
    </group>
  );
}

/** Floor customers don't use, hatched, so it's clear the checks leave it alone. */
export function StaffAreas({ handles }: { handles: StaffHandles }) {
  return (
    <group>
      {handles.areas.map((area, index) => <Area key={index} area={area} index={index} handles={handles} />)}
    </group>
  );
}
