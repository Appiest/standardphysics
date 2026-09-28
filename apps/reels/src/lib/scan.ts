import { useEffect, useState } from "react";
import { cancelRender, continueRender, delayRender, staticFile } from "remotion";
import { BufferAttribute, BufferGeometry } from "three";
import type { Point } from "./ink";

export type ScanObject = { name: string; footprint: Point[]; size: [number, number, number]; center: [number, number, number] };

export type FloorPlan = {
  walls: [Point, Point][];
  windows: [Point, Point][];
  doors: [Point, Point][];
  objects: ScanObject[];
  path: Point[];
  floorY: number;
};

async function loadBinary(url: string) {
  const response = await fetch(url);
  return response.arrayBuffer();
}

async function loadGeometry(name: string) {
  const [positions, indices] = await Promise.all([loadBinary(staticFile(`scan/${name}/positions.bin`)), loadBinary(staticFile(`scan/${name}/indices.bin`))]);
  const geometry = new BufferGeometry();
  geometry.setAttribute("position", new BufferAttribute(new Float32Array(positions), 3));
  geometry.setIndex(new BufferAttribute(new Uint32Array(indices), 1));
  geometry.computeVertexNormals();
  geometry.computeBoundingBox();
  return geometry;
}

async function loadPlan(name: string): Promise<FloorPlan> {
  const response = await fetch(staticFile(`scan/${name}/plan.json`));
  return response.json();
}

const geometryCache = new Map<string, Promise<BufferGeometry>>();
const planCache = new Map<string, Promise<FloorPlan>>();

function cached<T>(cache: Map<string, Promise<T>>, name: string, load: (name: string) => Promise<T>) {
  if (!cache.has(name)) cache.set(name, load(name));
  return cache.get(name)!;
}

function useLoaded<T>(label: string, load: () => Promise<T>) {
  const [value, setValue] = useState<T | null>(null);
  const [handle] = useState(() => delayRender(label));
  useEffect(() => {
    load()
      .then((loaded) => {
        setValue(loaded);
        continueRender(handle);
      })
      .catch((error) => cancelRender(error));
  }, [handle, load]);
  return value;
}

export function useScanGeometry(name: string) {
  const [load] = useState(() => () => cached(geometryCache, name, loadGeometry));
  return useLoaded(`LiDAR mesh ${name}`, load);
}

export function useFloorPlan(name: string) {
  const [load] = useState(() => () => cached(planCache, name, loadPlan));
  return useLoaded(`floor plan ${name}`, load);
}
