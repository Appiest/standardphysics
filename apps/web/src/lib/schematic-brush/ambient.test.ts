import { describe, expect, it } from "vitest";
import { AMBIENT_CELL_PX, buildFreeSpace, clearanceAt, planAmbientRoutes } from "./ambient";
import { SchematicBrush } from "./brush";
import { boxesOverlap, type Box } from "./geometry";

function seeded(seed: number) {
  let state = seed;
  return () => {
    state = (state * 1664525 + 1013904223) % 4294967296;
    return state / 4294967296;
  };
}

const PAGE_WIDTH = 1440;
const PAGE_HEIGHT = 900;
const CONTENT: Box[] = [
  [380, 60, 760, 140],
  [380, 150, 760, 620],
];

describe("ambient ink planning", () => {
  it("measures clearance as zero inside text and growing away from it", () => {
    const space = buildFreeSpace(PAGE_WIDTH, PAGE_HEIGHT, CONTENT);
    expect(clearanceAt(space, 500, 300)).toBe(0);
    expect(clearanceAt(space, 1100, 780)).toBeGreaterThan(clearanceAt(space, 790, 300));
  });

  it("keeps every route point away from the content", () => {
    const space = buildFreeSpace(PAGE_WIDTH, PAGE_HEIGHT, CONTENT);
    const routes = planAmbientRoutes(space, { random: seeded(7) });
    expect(routes.length).toBeGreaterThan(0);
    for (const [x, y] of routes.flat()) {
      const near: Box = [x - AMBIENT_CELL_PX * 2, y - AMBIENT_CELL_PX * 2, x + AMBIENT_CELL_PX * 2, y + AMBIENT_CELL_PX * 2];
      expect(CONTENT.some((box) => boxesOverlap(box, near))).toBe(false);
    }
  });

  it("keeps the pen in the lower part of the page", () => {
    const space = buildFreeSpace(PAGE_WIDTH, PAGE_HEIGHT, CONTENT);
    const points = Array.from({ length: 10 }, (_, seed) => planAmbientRoutes(space, { random: seeded(seed + 1) }).flat()).flat();
    expect(points.length).toBeGreaterThan(0);
    expect(Math.min(...points.map((point) => point[1]))).toBeGreaterThan(PAGE_HEIGHT * 0.5);
  });

  it("never records a mark that touches a keep-out", () => {
    const brush = new SchematicBrush({ unit: 12, measureText: (text, size) => text.length * size * 0.55, now: () => 0, speed: 2 });
    brush.setKeepOuts(CONTENT, [0, 0, PAGE_WIDTH, PAGE_HEIGHT]);
    const space = buildFreeSpace(PAGE_WIDTH, PAGE_HEIGHT, CONTENT);
    for (const route of planAmbientRoutes(space, { random: seeded(3) })) brush.draftRoute(route, 4000);
    expect(brush.activeStamps.length).toBeGreaterThan(0);
    expect(brush.inkTouches(CONTENT)).toBe(false);
  });
});
