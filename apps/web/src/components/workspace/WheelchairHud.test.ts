import { describe, expect, it } from "vitest";
import { POINTER_TAP_MAX_MS, shouldTapAfterPointerPress } from "./WheelchairHud";

describe("wheelchair steering button activation", () => {
  it("nudges for a short pointer activation", () => {
    expect(shouldTapAfterPointerPress(0)).toBe(true);
    expect(shouldTapAfterPointerPress(POINTER_TAP_MAX_MS - 1)).toBe(true);
  });

  it("does not add a tap after a held pointer activation", () => {
    expect(shouldTapAfterPointerPress(POINTER_TAP_MAX_MS)).toBe(false);
    expect(shouldTapAfterPointerPress(POINTER_TAP_MAX_MS + 500)).toBe(false);
  });
});
