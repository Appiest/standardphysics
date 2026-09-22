import { describe, expect, it } from "vitest";
import { isUsablePointerBox, pointerBoxLabel, sensorBoxFromPointer } from "@/lib/sensor-box";

describe("sensor box pointer math", () => {
  const rect = { left: 100, top: 50, width: 400, height: 300 };
  const natural = { width: 1200, height: 900 };

  it("scales a drawn rectangle from displayed pixels to stored-frame pixels", () => {
    const box = sensorBoxFromPointer({ x: 200, y: 125 }, { x: 300, y: 200 }, rect, natural);
    expect(box).toEqual({ left: 300, top: 225, right: 600, bottom: 450 });
  });

  it("normalizes any drag direction and clamps to the frame", () => {
    const box = sensorBoxFromPointer({ x: 560, y: 400 }, { x: 90, y: 40 }, rect, natural);
    expect(box).toEqual({ left: 0, top: 0, right: 1200, bottom: 900 });
  });

  it("rejects degenerate and non-finite boxes before a request is ever sent", () => {
    expect(isUsablePointerBox({ left: 0, top: 0, right: 0, bottom: 10 })).toBe(false);
    expect(isUsablePointerBox({ left: 0, top: 0, right: 10, bottom: -5 })).toBe(false);
    expect(isUsablePointerBox({ left: 0, top: 0, right: 10, bottom: 10 })).toBe(true);
    expect(isUsablePointerBox({ left: NaN, top: 0, right: 10, bottom: 10 })).toBe(false);
  });

  it("labels the box in stored-frame pixels so a mark is traceable", () => {
    expect(pointerBoxLabel({ left: 300, top: 225, right: 600, bottom: 450 })).toBe(
      "Frame pixels 300–600 across, 225–450 down"
    );
  });
});
