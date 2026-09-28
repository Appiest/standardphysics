import { Vector3 } from "three";
import { describe, expect, it } from "vitest";
import { cameraWalls, clearDistance, placeCamera, WALL_CLEARANCE } from "@/lib/camera-walls";
import type { SceneGraph, SceneNode } from "@/types/contracts";

const HALF_ROOM = 3;
const THICKNESS = 0.2;
const CUT = 2.2;

function node(id: string, kind: string, dimensions: SceneNode["dimensions"], m: SceneNode["transform"]["m"]): SceneNode {
  return { id, kind, label: kind, raw_category: kind, dimensions, transform: { m }, quality: "measured", movable: false, labeled_by: "roomplan", parent_id: null };
}

function wallAlongX(id: string, y: number): SceneNode {
  return node(id, "wall", { x: 6, y: THICKNESS, z: 2.8 }, [1, 0, 0, 0, 0, 1, 0, y, 0, 0, 1, 1.4, 0, 0, 0, 1]);
}

function wallAlongY(id: string, x: number): SceneNode {
  return node(id, "wall", { x: 6, y: THICKNESS, z: 2.8 }, [0, -1, 0, x, 1, 0, 0, 0, 0, 0, 1, 1.4, 0, 0, 0, 1]);
}

/** A six-metre square room, centred on the origin, with 2.8 m walls. */
function room(extra: SceneNode[] = []): SceneGraph {
  return {
    scan_id: "scan-1", revision: 0, base_hash: null,
    nodes: [wallAlongX("north", HALF_ROOM), wallAlongX("south", -HALF_ROOM), wallAlongY("east", HALF_ROOM), wallAlongY("west", -HALF_ROOM), ...extra],
  };
}

const target = new Vector3(0, 0.8, 0);
const insideFace = HALF_ROOM - THICKNESS / 2 - WALL_CLEARANCE;

describe("placeCamera", () => {
  it("leaves the camera where it is when no wall is in the way", () => {
    const walls = cameraWalls(room(), CUT);
    const placed = placeCamera(target, new Vector3(1, 1.2, 0), 1.08, walls, false);
    expect(placed.distanceTo(target)).toBeCloseTo(1.08, 6);
  });

  it("stops in front of a wall instead of passing through it", () => {
    const walls = cameraWalls(room(), CUT);
    const placed = placeCamera(target, new Vector3(10, 1, 0), 10, walls, false);
    expect(placed.x).toBeCloseTo(insideFace, 3);
  });

  it("stops in front of a wall hidden behind a doorway's gap as well", () => {
    const door = node("door", "door", { x: 1, y: THICKNESS, z: 2 }, [0, -1, 0, HALF_ROOM, 1, 0, 0, 0, 0, 0, 1, 1, 0, 0, 0, 1]);
    const walls = cameraWalls(room([door]), CUT);
    expect(placeCamera(target, new Vector3(10, 1, 0), 10, walls, false).x).toBeCloseTo(insideFace, 3);
  });

  it("lets the camera look over the cut-down walls from outside, as in the dollhouse view", () => {
    const walls = cameraWalls(room(), CUT);
    const overhead = new Vector3(6, 8, 6);
    const placed = placeCamera(target, overhead, overhead.distanceTo(target), walls, false);
    expect(placed.distanceTo(overhead)).toBeCloseTo(0, 6);
  });

  it("lifts the camera over the walls when the owner zooms out", () => {
    const walls = cameraWalls(room(), CUT);
    const arm = 9;
    const placed = placeCamera(target, new Vector3(9, 0.9, 0), arm, walls, true);
    expect(placed.distanceTo(target)).toBeCloseTo(arm, 6);
    expect(placed.y).toBeGreaterThan(CUT);
    expect(clearDistance(target, placed.clone().sub(target).normalize(), walls)).toBeGreaterThanOrEqual(arm);
  });

  it("ignores furniture, so a misplaced or floating box cannot pin the camera", () => {
    const floating = node("box", "object", { x: 1, y: 1, z: 1 }, [1, 0, 0, 1.5, 0, 1, 0, 0, 0, 0, 1, 1.5, 0, 0, 0, 1]);
    const walls = cameraWalls(room([floating]), CUT);
    const placed = placeCamera(target, new Vector3(2.5, 1, 0), 2.5, walls, false);
    expect(placed.distanceTo(target)).toBeCloseTo(2.5, 6);
  });

  it("ignores a wall the camera is looking from inside, so a bad wall never traps it", () => {
    const walls = cameraWalls(room(), CUT);
    const inWall = new Vector3(HALF_ROOM, 1, 0);
    const placed = placeCamera(inWall, new Vector3(HALF_ROOM, 1, 2), 2, walls, false);
    expect(placed.distanceTo(inWall)).toBeCloseTo(2, 6);
  });
});
