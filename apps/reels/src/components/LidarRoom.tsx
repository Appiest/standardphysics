import { useThree } from "@react-three/fiber";
import { ThreeCanvas } from "@remotion/three";
import { useLayoutEffect, useMemo, useState } from "react";
import { continueRender, delayRender } from "remotion";
import { Color, DoubleSide, ShaderMaterial, Vector2, Vector3, type BufferGeometry, type PerspectiveCamera } from "three";
import { useScanGeometry } from "../lib/scan";

export type OrbitCamera = { azimuth: number; elevation: number; distance: number; target?: [number, number, number]; fov?: number };

type LidarRoomProps = {
  scan: string;
  width: number;
  height: number;
  camera: OrbitCamera;
  /** Where the scan plane has reached, from 0 (nothing measured) to 1 (the whole room). */
  reveal: number;
  /** Height above the floor, in metres, where the room is cut open so the camera can look in. */
  cutaway?: number;
  /** How far from the room's middle, in metres, the scan fades out, so its ragged outer edge never shows. */
  radius?: number;
  /** 0 draws the mesh as shaded clay, 1 as a see-through wire of what the phone measured. */
  wire?: number;
};

const vertexShader = /* glsl */ `
  varying vec3 vWorld;
  varying vec3 vNormal;
  void main() {
    vec4 world = modelMatrix * vec4(position, 1.0);
    vWorld = world.xyz;
    vNormal = normalize(mat3(modelMatrix) * normal);
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`;

const fragmentShader = /* glsl */ `
  uniform float uSweep;
  uniform float uCut;
  uniform float uFloor;
  uniform float uWire;
  uniform vec2 uCenter;
  uniform float uRadius;
  uniform vec3 uPaper;
  uniform vec3 uShadow;
  uniform vec3 uInk;
  uniform vec3 uTape;
  varying vec3 vWorld;
  varying vec3 vNormal;
  void main() {
    if (vWorld.z > uSweep || vWorld.y > uCut) discard;
    vec3 n = normalize(vNormal);
    if (!gl_FrontFacing) n = -n;
    float key = max(dot(n, normalize(vec3(0.7, 0.55, 0.45))), 0.0);
    float fill = max(dot(n, normalize(vec3(-0.5, 0.35, -0.6))), 0.0);
    float light = clamp(0.18 + key * 0.8 + fill * 0.22, 0.0, 1.0);
    vec3 viewDir = normalize(cameraPosition - vWorld);
    float rim = 1.0 - abs(dot(n, viewDir));
    vec3 clay = mix(uShadow, uPaper, smoothstep(0.2, 0.9, light));
    clay = mix(clay, uInk, smoothstep(0.55, 0.9, rim) * 0.9);
    float floorShade = smoothstep(0.0, 0.5, vWorld.y - uFloor);
    clay *= mix(0.82, 1.0, floorShade);
    float lines = 1.0 - smoothstep(0.0, 0.035, abs(fract(vWorld.y * 6.0) - 0.5) - 0.44);
    vec3 wire = mix(uPaper, uInk, lines * 0.8);
    vec3 color = mix(clay, wire, uWire);
    float front = uSweep - vWorld.z;
    float glow = exp(-front * 7.0);
    color = mix(color, uTape, clamp(glow * 1.15, 0.0, 1.0));
    color += uTape * exp(-front * 30.0) * 0.8;
    float reach = length(vWorld.xz - uCenter);
    float alpha = 1.0 - smoothstep(uRadius * 0.8, uRadius, reach);
    if (alpha <= 0.01) discard;
    gl_FragColor = vec4(color, alpha);
  }
`;

function sweepRange(geometry: BufferGeometry) {
  const box = geometry.boundingBox!;
  return [box.min.z - 0.05, box.max.z + 0.05] as const;
}

function useRoomMaterial() {
  return useMemo(
    () =>
      new ShaderMaterial({
        vertexShader,
        fragmentShader,
        side: DoubleSide,
        transparent: true,
        uniforms: {
          uSweep: { value: 0 },
          uCut: { value: 100 },
          uFloor: { value: 0 },
          uCenter: { value: new Vector2() },
          uRadius: { value: 100 },
          uWire: { value: 0 },
          uPaper: { value: new Color("#f3f3f1") },
          uShadow: { value: new Color("#8c8c85") },
          uInk: { value: new Color("#0d0d0c") },
          uTape: { value: new Color("#f6be1a") },
        },
      }),
    [],
  );
}

function orbitPosition({ azimuth, elevation, distance }: OrbitCamera, target: Vector3) {
  return new Vector3(
    target.x + distance * Math.cos(elevation) * Math.sin(azimuth),
    target.y + distance * Math.sin(elevation),
    target.z + distance * Math.cos(elevation) * Math.cos(azimuth),
  );
}

function CameraRig({ position, target, fov }: { position: Vector3; target: Vector3; fov: number }) {
  const camera = useThree((state) => state.camera) as PerspectiveCamera;
  camera.position.copy(position);
  camera.fov = fov;
  camera.near = 0.05;
  camera.far = 200;
  camera.lookAt(target);
  camera.updateProjectionMatrix();
  return null;
}

type RoomProps = { geometry: BufferGeometry; camera: OrbitCamera; reveal: number; cutaway: number; wire: number; radius: number };

/** Draws the scene synchronously after every update, so a frame is never captured before WebGL has painted it. */
function usePaintEveryUpdate() {
  const { gl, scene, camera } = useThree();
  const [firstPaint] = useState(() => delayRender("First LiDAR paint"));
  useLayoutEffect(() => {
    gl.render(scene, camera);
    continueRender(firstPaint);
  });
}

function Room({ geometry, camera, reveal, cutaway, wire, radius }: RoomProps) {
  const material = useRoomMaterial();
  usePaintEveryUpdate();
  const [near, far] = sweepRange(geometry);
  const floor = geometry.boundingBox!.min.y;
  material.uniforms.uSweep.value = near + (far - near) * reveal;
  material.uniforms.uCut.value = floor + cutaway;
  material.uniforms.uWire.value = wire;
  material.uniforms.uFloor.value = floor;
  const center = geometry.boundingBox!.getCenter(new Vector3());
  material.uniforms.uCenter.value.set(center.x, center.z);
  material.uniforms.uRadius.value = radius;
  const target = camera.target ? new Vector3(...camera.target) : new Vector3(center.x, floor + 0.6, center.z);
  const position = orbitPosition(camera, target);
  return (
    <>
      <CameraRig position={position} target={target} fov={camera.fov ?? 32} />
      <mesh geometry={geometry} material={material} />
    </>
  );
}

export function LidarRoom({ scan, width, height, camera, reveal, cutaway = 100, wire = 0, radius = 100 }: LidarRoomProps) {
  const geometry = useScanGeometry(scan);
  return (
    <ThreeCanvas width={width} height={height} linear flat gl={{ antialias: true }}>
      {geometry && <Room geometry={geometry} camera={camera} reveal={reveal} cutaway={cutaway} wire={wire} radius={radius} />}
    </ThreeCanvas>
  );
}
