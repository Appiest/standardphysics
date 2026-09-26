import { useThree } from "@react-three/fiber";
import { ThreeCanvas } from "@remotion/three";
import { useEffect, useLayoutEffect, useState } from "react";
import { cancelRender, continueRender, delayRender, staticFile } from "remotion";
import { Box3, Mesh, MeshBasicMaterial, Plane, Vector3, type Group, type Material, type PerspectiveCamera, type Texture } from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import type { OrbitCamera } from "./LidarRoom";

type TexturedRoomProps = {
  scan: string;
  width: number;
  height: number;
  camera: OrbitCamera;
  /** Height above the floor, in metres, where the model is cut open so the camera can look in. */
  cutaway?: number;
};

type LoadedRoom = { model: Group; bounds: Box3; ceiling: Plane };

const rooms = new Map<string, Promise<LoadedRoom>>();

/** The photos already carry the room's light, so every surface is drawn unlit, straight from its texture. */
function unlit(model: Group, ceiling: Plane) {
  model.traverse((node) => {
    if (!(node instanceof Mesh)) return;
    const source = node.material as Material & { map?: Texture | null };
    node.material = new MeshBasicMaterial({ map: source.map ?? null, clippingPlanes: [ceiling] });
  });
}

async function loadRoom(scan: string): Promise<LoadedRoom> {
  const gltf = await new GLTFLoader().loadAsync(staticFile(`scan/${scan}/textured.glb`));
  const bounds = new Box3().setFromObject(gltf.scene);
  const ceiling = new Plane(new Vector3(0, -1, 0), 100);
  unlit(gltf.scene, ceiling);
  return { model: gltf.scene, bounds, ceiling };
}

function useRoom(scan: string) {
  const [room, setRoom] = useState<LoadedRoom | null>(null);
  const [handle] = useState(() => delayRender(`Textured room ${scan}`, { timeoutInMilliseconds: 120000 }));
  useEffect(() => {
    if (!rooms.has(scan)) rooms.set(scan, loadRoom(scan));
    rooms
      .get(scan)!
      .then((loaded) => {
        setRoom(loaded);
        continueRender(handle);
      })
      .catch((error) => cancelRender(error));
  }, [scan, handle]);
  return room;
}

function orbit({ azimuth, elevation, distance }: OrbitCamera, target: Vector3) {
  return new Vector3(
    target.x + distance * Math.cos(elevation) * Math.sin(azimuth),
    target.y + distance * Math.sin(elevation),
    target.z + distance * Math.cos(elevation) * Math.cos(azimuth),
  );
}

function Scene({ room, camera, cutaway }: { room: LoadedRoom; camera: OrbitCamera; cutaway: number }) {
  const { gl, scene, camera: view } = useThree();
  const [firstPaint] = useState(() => delayRender("First textured paint"));
  const center = room.bounds.getCenter(new Vector3());
  const target = camera.target ? new Vector3(...camera.target) : new Vector3(center.x, room.bounds.min.y + 0.6, center.z);
  room.ceiling.constant = room.bounds.min.y + cutaway;
  gl.localClippingEnabled = true;
  const perspective = view as PerspectiveCamera;
  perspective.position.copy(orbit(camera, target));
  perspective.fov = camera.fov ?? 32;
  perspective.near = 0.1;
  perspective.far = 400;
  perspective.lookAt(target);
  perspective.updateProjectionMatrix();
  useLayoutEffect(() => {
    gl.render(scene, view);
    continueRender(firstPaint);
  });
  return <primitive object={room.model} />;
}

/** The photo-textured model the app builds from a scan, lit only by the photos it was painted from. */
export function TexturedRoom({ scan, width, height, camera, cutaway = 100 }: TexturedRoomProps) {
  const room = useRoom(scan);
  return (
    <ThreeCanvas width={width} height={height} linear flat gl={{ antialias: true }}>
      {room && <Scene room={room} camera={camera} cutaway={cutaway} />}
    </ThreeCanvas>
  );
}
