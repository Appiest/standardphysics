import { describe, expect, it } from "vitest";
import { containedContentRect, isUsablePointerBox, overlayGeometryFor, pointerBoxLabel, sensorBoxFromPointer } from "@/lib/sensor-box";

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

  it("maps through the object-contain letterbox when the photo does not fill the element", () => {
    const element = { left: 0, top: 0, width: 400, height: 200 };
    const content = containedContentRect(element, natural);
    expect(content.height).toBeCloseTo(200, 2);
    expect(content.width).toBeCloseTo(266.667, 2);
    expect(content.left).toBeCloseTo(66.667, 2);

    const box = sensorBoxFromPointer({ x: 100, y: 50 }, { x: 300, y: 150 }, element, natural);
    expect(box.left).toBeCloseTo(150, 0);
    expect(box.top).toBeCloseTo(225, 0);
    expect(box.right).toBeCloseTo(1050, 0);
    expect(box.bottom).toBeCloseTo(675, 0);
  });

  it("a pointer in the letterbox bars clamps to the photo edge instead of inventing a position", () => {
    const element = { left: 0, top: 0, width: 400, height: 200 };
    const box = sensorBoxFromPointer({ x: 0, y: 0 }, { x: 300, y: 150 }, element, natural);
    expect(box.left).toBe(0);
    expect(box.top).toBe(0);
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

describe("mark overlay geometry", () => {
  const frame = { width: 1920, height: 1440 };

  it("paints the draft box over the same content the pointer math uses, case aspect-fit", () => {
    const rect = { left: 480, top: 300, width: 480, height: 360 };
    const box = sensorBoxFromPointer({ x: 528, y: 345 }, { x: 720, y: 495 }, rect, frame);
    const overlay = overlayGeometryFor(rect, frame, box);
    expect(overlay).not.toBeNull();
    expect(overlay!.content).toEqual({ left: 0, top: 0, width: 100, height: 100 });
    expect(overlay!.box).not.toBeNull();
    const boxView = overlay!.box!;
    expect(boxView.left).toBeCloseTo(10, 6);
    expect(boxView.top).toBeCloseTo(12.5, 6);
    expect(boxView.width).toBeCloseTo(40, 6);
    expect(boxView.height).toBeCloseTo((150 / 360) * 100, 6);
  });

  it("letterboxes the overlay exactly where the photo sits inside a mismatched surface", () => {
    const rect = { left: 0, top: 0, width: 400, height: 200 };
    const overlay = overlayGeometryFor(rect, frame, { left: 960, top: 0, right: 1920, bottom: 1440 });
    expect(overlay).not.toBeNull();
    const content = overlay!.content;
    expect(content.left).toBeCloseTo(16.667, 3);
    expect(content.top).toBeCloseTo(0, 3);
    expect(content.width).toBeCloseTo(66.667, 3);
    const boxView = overlay!.box!;
    expect(boxView.left).toBeCloseTo(50, 3);
    expect(boxView.width).toBeCloseTo(50, 3);
  });

  it("a reversed drag still shows a positive rectangle", () => {
    const rect = { left: 0, top: 0, width: 480, height: 360 };
    const box = sensorBoxFromPointer({ x: 400, y: 300 }, { x: 80, y: 60 }, rect, frame);
    expect(box.left).toBeLessThan(box.right);
    expect(box.top).toBeLessThan(box.bottom);
    const overlay = overlayGeometryFor(rect, frame, box);
    expect(overlay).not.toBeNull();
    expect(overlay!.box!.width).toBeGreaterThan(0);
    expect(overlay!.box!.height).toBeGreaterThan(0);
  });

  it("frame switch (null box) renders no phantom region and a degenerate surface renders nothing", () => {
    const rect = { left: 0, top: 0, width: 480, height: 360 };
    const fresh = overlayGeometryFor(rect, frame, null);
    expect(fresh).not.toBeNull();
    expect(fresh!.box).toBeNull();
    expect(overlayGeometryFor(rect, frame, { left: 5, top: 5, right: 5, bottom: 30 })!.box).toBeNull();
    expect(overlayGeometryFor({ left: 0, top: 0, width: 0, height: 0 }, frame, { left: 0, top: 0, right: 10, bottom: 10 })).toBeNull();
  });
});
