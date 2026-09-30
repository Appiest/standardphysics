import { describe, expect, it } from "vitest";
import type { SceneGraph, SceneNode } from "@/types/contracts";
import {
  drawOrder, foldToQuarter, hasMoved, labelBesideLine, placeLabels, planRole, planTurnDegrees, roomPointFrom, turnedBounds, turnedPoint,
} from "./plan-view";

function turnedBy(degrees: number, x: number, y: number, z = 0): SceneNode["transform"] {
  const radians = (degrees * Math.PI) / 180;
  const [cos, sin] = [Math.cos(radians), Math.sin(radians)];
  return { m: [cos, -sin, 0, x, sin, cos, 0, y, 0, 0, 1, z, 0, 0, 0, 1] };
}

function node(id: string, fields: Partial<SceneNode>): SceneNode {
  return {
    id, kind: "object", label: "Table", raw_category: "table", quality: "measured", movable: true, labeled_by: "roomplan", parent_id: null,
    dimensions: { x: 1, y: 1, z: 0.75 }, transform: turnedBy(0, 0, 0, 0.375), ...fields,
  };
}

function wall(id: string, degrees: number, length: number, x = 0, y = 0): SceneNode {
  return node(id, { kind: "wall", label: "Wall", raw_category: "wall", movable: false, dimensions: { x: length, y: 0, z: 3 }, transform: turnedBy(degrees, x, y, 1.5) });
}

const room = (nodes: SceneNode[]): SceneGraph => ({ scan_id: "s", revision: 0, base_hash: null, nodes });
const rounded = (value: number) => +value.toFixed(6) + 0;

describe("foldToQuarter", () => {
  it("folds any wall direction into [-45, 45)", () => {
    expect([10, 100, -80, 190, 45, -45, 135, -27.6].map(foldToQuarter).map(rounded)).toEqual([10, 10, 10, 10, -45, -45, -45, -27.6]);
  });
});

describe("planTurnDegrees", () => {
  it("lines up a phone scan whose walls sit near -26 degrees, as the owner's shop did", () => {
    const walls = [wall("a", -27.6, 9.67), wall("b", -115.6, 2.95), wall("c", 154.4, 1.24), wall("d", 151.6, 9.92), wall("e", -25.6, 1.01)];
    const turn = planTurnDegrees(room(walls));
    expect(turn).toBeGreaterThan(-28.4);
    expect(turn).toBeLessThan(-25.6);
  });

  it("lets long walls outweigh a short jog", () => {
    expect(rounded(planTurnDegrees(room([wall("long", 0, 10), wall("short", 30, 0.5)])))).toBeLessThan(2);
  });

  it("leaves a room with no walls untouched", () => {
    expect(planTurnDegrees(room([node("t", {})]))).toBe(0);
  });

  it("draws every wall square with the screen once turned", () => {
    const directions = [20, 110, -70];
    const turn = planTurnDegrees(room(directions.map((degrees, index) => wall(`w${index}`, degrees, 4))));
    for (const degrees of directions) {
      const radians = (degrees * Math.PI) / 180;
      const end = turnedPoint({ x: Math.cos(radians), y: Math.sin(radians) }, turn);
      const onScreen = (Math.atan2(end.y, end.x) * 180) / Math.PI;
      expect(Math.abs(rounded(foldToQuarter(onScreen)))).toBeLessThan(1e-6);
    }
  });
});

describe("turnedBounds", () => {
  it("fits a room drawn at an angle snugly once it is turned square", () => {
    const floor = node("f", { kind: "floor", label: "Floor", dimensions: { x: 4, y: 2, z: 0 }, transform: turnedBy(30, 0, 0) });
    const box = turnedBounds([floor], 30, 0);
    expect([box.width, box.height].map(rounded)).toEqual([4, 2]);
  });

  it("keeps north up with no turn, flipping y for the screen", () => {
    const box = turnedBounds([node("t", { transform: turnedBy(0, 2, 3) })], 0, 0);
    expect([box.minX, box.minY, box.width, box.height].map(rounded)).toEqual([1.5, -3.5, 1, 1]);
  });
});

describe("roomPointFrom", () => {
  it("undoes the plan's turn and flip, so a drag moves the piece in room metres", () => {
    const turn = 30;
    const scale = 50;
    const radians = (turn * Math.PI) / 180;
    const [cos, sin] = [Math.cos(radians), Math.sin(radians)];
    const toScreen = (room: { x: number; y: number }) => {
      const drawn = turnedPoint(room, turn);
      return { clientX: 100 + scale * drawn.x, clientY: 200 + scale * drawn.y };
    };
    const inverse = {
      a: cos / scale, b: -sin / scale, c: sin / scale, d: cos / scale,
      e: -(cos * 100 + sin * 200) / scale, f: (sin * 100 - cos * 200) / scale,
    };
    const point = roomPointFrom(inverse, toScreen({ x: 1.25, y: -0.5 }));
    expect([point.x, point.y].map(rounded)).toEqual([1.25, -0.5]);
  });
});

describe("planRole", () => {
  const floor = 0;

  it("lets the pointer through a laptop resting on a table, so the table is what gets grabbed", () => {
    const laptop = node("l", { label: "Laptop", raw_category: "laptop", dimensions: { x: 0.4, y: 0.3, z: 0.04 }, transform: turnedBy(0, 0, 0, 0.77) });
    expect(planRole(laptop, floor)).toBe("riding");
  });

  it("keeps floor-standing, built-in and unlisted pieces as they were", () => {
    expect(planRole(node("t", {}), floor)).toBe("movable");
    expect(planRole(node("c", { label: "Counter", raw_category: "storage", movable: false }), floor)).toBe("fixed");
    expect(planRole(wall("w", 0, 3), floor)).toBe("backdrop");
  });
});

describe("drawOrder", () => {
  it("draws the bigger piece last among movable ones, so it wins the grab where they overlap", () => {
    const caseNode = node("case", { label: "Display case", dimensions: { x: 0.9, y: 0.4, z: 0.7 } });
    const stool = node("stool", { label: "Stool", dimensions: { x: 0.35, y: 0.35, z: 0.6 } });
    const drawer = node("drawer", { label: "Drawer", raw_category: "drawer", dimensions: { x: 0.7, y: 0.4, z: 0.3 }, transform: turnedBy(0, 0, 0, 0.6) });
    const order = drawOrder([caseNode, drawer, stool, wall("w", 0, 3)], 0).map((piece) => piece.id);
    expect(order).toEqual(["w", "stool", "case", "drawer"]);
  });
});

describe("hasMoved", () => {
  it("notices a slide or a turn, and not a full circle", () => {
    const scanned = node("t", { transform: turnedBy(10, 1, 1) });
    expect(hasMoved(scanned, node("t", { transform: turnedBy(10, 1.2, 1) }))).toBe(true);
    expect(hasMoved(scanned, node("t", { transform: turnedBy(25, 1, 1) }))).toBe(true);
    expect(hasMoved(scanned, node("t", { transform: turnedBy(370, 1, 1) }))).toBe(false);
  });
});

describe("labelBesideLine", () => {
  it("puts a reading above a line that runs across", () => {
    expect(labelBesideLine({ x: 0, y: 1 }, { x: 2, y: 1 }, "23.7 in", 0.3, 5)).toEqual({ x: 1, y: 1 - 0.18 });
  });

  it("puts a reading beside a line that runs up and down, on the side toward the room", () => {
    const right = labelBesideLine({ x: 0, y: 0 }, { x: 0, y: 2 }, "29.7 in", 0.3, 5);
    const left = labelBesideLine({ x: 0, y: 0 }, { x: 0, y: 2 }, "29.7 in", 0.3, -5);
    expect(right.x).toBeGreaterThan(0.3 * 0.62 * 3.5);
    expect(left.x).toBeLessThan(-0.3 * 0.62 * 3.5);
    expect(right.y).toBeCloseTo(1.105);
  });
});

describe("placeLabels", () => {
  const size = 0.3;

  it("draws one label where two findings measured the same spot", () => {
    const labels = placeLabels([
      { key: "a", text: "23.7 in", x: 1, y: 1 },
      { key: "b", text: "23.7 in", x: 1.05, y: 1.02 },
    ], size);
    expect(labels.map((label) => label.key)).toEqual(["a"]);
  });

  it("slides different readings apart until neither covers the other", () => {
    const labels = placeLabels([
      { key: "a", text: "23.7 in", x: 1, y: 1 },
      { key: "b", text: "31.2 in", x: 1.1, y: 1.05 },
    ], size);
    expect(labels).toHaveLength(2);
    expect(Math.abs(labels[1].y - labels[0].y)).toBeGreaterThanOrEqual(1.25 * size - 1e-9);
    expect(labels[0]).toEqual({ key: "a", text: "23.7 in", x: 1, y: 1 });
  });

  it("leaves labels that are far apart where they are", () => {
    const apart = [{ key: "a", text: "23.7 in", x: 0, y: 0 }, { key: "b", text: "23.7 in", x: 5, y: 0 }];
    expect(placeLabels(apart, size)).toEqual(apart);
  });
});
