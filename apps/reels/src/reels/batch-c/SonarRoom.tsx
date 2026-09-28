import { useThree } from "@react-three/fiber";
import { ThreeCanvas } from "@remotion/three";
import { useLayoutEffect, useMemo, useState } from "react";
import { continueRender, delayRender } from "remotion";
import { Color, DoubleSide, ShaderMaterial, Vector2, Vector3, type BufferGeometry, type PerspectiveCamera } from "three";
import { useScanGeometry } from "../../lib/scan";

export type SonarCamera = { azimuth: number; elevation: number; distance: number; fov?: number };

type SonarRoomProps = {
  scan: string;
  width: number;
  height: number;
  camera: SonarCamera;
  /** How far the measuring ring has spread from the room's middle, in metres. */
  front: number;
  /** How far the erasing ring has spread behind it, in metres; nothing inside it is drawn. */
  trailing: number;
  cutaway: number;
  radius: number;
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
  uniform float uFront;
  uniform float uTrailing;
  uniform float uCut;
  uniform float uFloor;
  uniform float uRadius;
  uniform vec2 uCenter;
  uniform vec3 uPaper;
  uniform vec3 uShadow;
  uniform vec3 uInk;
  uniform vec3 uTape;
  varying vec3 vWorld;
  varying vec3 vNormal;
  void main() {
    float reach = length(vWorld.xz - uCenter);
    if (reach > uFront || reach < uTrailing || vWorld.y > uCut || reach > uRadius) discard;
    vec3 n = normalize(vNormal);
    if (!gl_FrontFacing) n = -n;
    float key = max(dot(n, normalize(vec3(0.7, 0.55, 0.45))), 0.0);
    float fill = max(dot(n, normalize(vec3(-0.5, 0.35, -0.6))), 0.0);
    float light = clamp(0.18 + key * 0.8 + fill * 0.22, 0.0, 1.0);
    vec3 viewDir = normalize(cameraPosition - vWorld);
    float rim = 1.0 - abs(dot(n, viewDir));
    vec3 clay = mix(uShadow, uPaper, smoothstep(0.2, 0.9, light));
    clay = mix(clay, uInk, smoothstep(0.55, 0.9, rim) * 0.9);
    clay *= mix(0.82, 1.0, smoothstep(0.0, 0.5, vWorld.y - uFloor));
    float ahead = uFront - reach;
    float behind = reach - uTrailing;
    float erasing = step(0.001, uTrailing);
    float glow = exp(-ahead * 6.0) + exp(-behind * 6.0) * 0.8 * erasing;
    vec3 color = mix(clay, uTape, clamp(glow, 0.0, 1.0));
    color += uTape * (exp(-ahead * 28.0) + exp(-behind * 28.0) * erasing) * 0.9;
    gl_FragColor = vec4(color, 1.0 - smoothstep(uRadius * 0.82, uRadius, reach));
  }
`;

function useSonarMaterial() {
  return useMemo(
    () =>
      new ShaderMaterial({
        vertexShader,
        fragmentShader,
        side: DoubleSide,
        transparent: true,
        uniforms: {
          uFront: { value: 0 },
          uTrailing: { value: 0 },
          uCut: { value: 100 },
          uFloor: { value: 0 },
          uRadius: { value: 100 },
          uCenter: { value: new Vector2() },
          uPaper: { value: new Color("#f3f3f1") },
          uShadow: { value: new Color("#8c8c85") },
          uInk: { value: new Color("#0d0d0c") },
          uTape: { value: new Color("#f6be1a") },
        },
      }),
    [],
  );
}

function CameraRig({ camera, target }: { camera: SonarCamera; target: Vector3 }) {
  const view = useThree((state) => state.camera) as PerspectiveCamera;
  const { azimuth, elevation, distance } = camera;
  view.position.set(
    target.x + distance * Math.cos(elevation) * Math.sin(azimuth),
    target.y + distance * Math.sin(elevation),
    target.z + distance * Math.cos(elevation) * Math.cos(azimuth),
  );
  view.fov = camera.fov ?? 32;
  view.near = 0.05;
  view.far = 200;
  view.lookAt(target);
  view.updateProjectionMatrix();
  return null;
}

type RoomProps = Omit<SonarRoomProps, "scan" | "width" | "height"> & { geometry: BufferGeometry };

/** Draws the scene synchronously after every update, so a frame is never captured before WebGL has painted it. */
function usePaintEveryUpdate() {
  const { gl, scene, camera } = useThree();
  const [firstPaint] = useState(() => delayRender("First LiDAR paint"));
  useLayoutEffect(() => {
    gl.render(scene, camera);
    continueRender(firstPaint);
  });
}

function Room({ geometry, camera, front, trailing, cutaway, radius }: RoomProps) {
  const material = useSonarMaterial();
  usePaintEveryUpdate();
  const box = geometry.boundingBox!;
  const center = box.getCenter(new Vector3());
  const floor = box.min.y;
  const uniforms = material.uniforms;
  uniforms.uFront.value = front;
  uniforms.uTrailing.value = trailing;
  uniforms.uCut.value = floor + cutaway;
  uniforms.uFloor.value = floor;
  uniforms.uRadius.value = radius;
  uniforms.uCenter.value.set(center.x, center.z);
  return (
    <>
      <CameraRig camera={camera} target={new Vector3(center.x, floor + 0.6, center.z)} />
      <mesh geometry={geometry} material={material} />
    </>
  );
}

/** The LiDAR room revealed by a ring of light spreading from its middle, and optionally erased by a second ring following it. */
export function SonarRoom({ scan, width, height, ...room }: SonarRoomProps) {
  const geometry = useScanGeometry(scan);
  return (
    <ThreeCanvas width={width} height={height} linear flat gl={{ antialias: true }}>
      {geometry && <Room geometry={geometry} {...room} />}
    </ThreeCanvas>
  );
}
