import { describe, expect, it } from "vitest";
import { SchematicBrush } from "./brush";
import { cutPolyline, polylineLength, splitDashes, type Point } from "./geometry";
import { demoRoute, zoneAt } from "./sheet";
import { inchesForLength } from "./vocabulary";

const UNIT = 12;

function brushAt(clock: { now: number }) {
  return new SchematicBrush({ unit: UNIT, measureText: (text, size) => text.length * size * 0.55, now: () => clock.now });
}

describe("schematic geometry", () => {
  it("cuts a polyline at a pen-travel budget", () => {
    const route: Point[] = [[0, 0], [10, 0], [10, 10]];
    expect(cutPolyline(route, 15)).toEqual([[0, 0], [10, 0], [10, 5]]);
    expect(cutPolyline(route, 0)).toEqual([]);
  });

  it("splits a line into dashes that add up to the drawn share", () => {
    const dashes = splitDashes([[0, 0], [100, 0]], 6, 4);
    expect(dashes).toHaveLength(10);
    expect(dashes.reduce((sum, dash) => sum + polylineLength(dash), 0)).toBeCloseTo(60);
  });
});

describe("sheet scale", () => {
  it("reads dimensions in inches at six inches per grid unit", () => {
    expect(inchesForLength(UNIT * 6, UNIT)).toBe('36"');
    expect(inchesForLength(UNIT * 5, UNIT)).toBe('30"');
  });

  it("names the drafting zone under the pen", () => {
    expect(zoneAt(10, 10, 800, 600)).toBe("A1");
    expect(zoneAt(799, 599, 800, 600)).toBe("F8");
  });
});

describe("SchematicBrush", () => {
  it("schedules nodes along a route to draw in one after another", () => {
    const clock = { now: 1000 };
    const brush = brushAt(clock);
    brush.draftRoute(demoRoute(1200, 800), 9000);

    expect(brush.activeStamps.length).toBeGreaterThan(10);
    const inkMarks = brush.activeStamps.flatMap((stamp) => stamp.marks).filter((mark) => mark.kind === "ink");
    expect(inkMarks.length).toBeGreaterThan(0);
    const starts = brush.activeStamps.map((stamp) => stamp.startsAt);
    expect(starts).toEqual([...starts].sort((a, b) => a - b));
    expect(starts.at(-1)).toBeGreaterThan(clock.now + 8000);
  });

  it("follows the cursor with a lag and bakes stamps once their pen finishes", () => {
    const clock = { now: 0 };
    const brush = brushAt(clock);
    brush.pointerDown(0, 0);
    brush.pointerMove(600, 0);
    brush.tick();
    const stampsAfterOneFrame = brush.activeStamps.length;

    for (let frame = 0; frame < 120; frame++) brush.tick();
    brush.pointerUp();
    expect(brush.activeStamps.length).toBeGreaterThan(stampsAfterOneFrame);

    clock.now = 60_000;
    expect(brush.tick().length).toBeGreaterThan(3);
    expect(brush.activeStamps).toHaveLength(0);
  });
});
