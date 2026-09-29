import { describe, expect, it } from "vitest";
import { endsADrag, TAP_SLOP_PX } from "./tap";

describe("a click on the model", () => {
  it("is a tap when the pointer barely moved between press and release", () => {
    expect(endsADrag({ delta: 0 })).toBe(false);
    expect(endsADrag({ delta: TAP_SLOP_PX })).toBe(false);
  });

  it("ends a camera drag when the pointer travelled further than a tap", () => {
    expect(endsADrag({ delta: TAP_SLOP_PX + 1 })).toBe(true);
    expect(endsADrag({ delta: 180 })).toBe(true);
  });
});
