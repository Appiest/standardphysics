import { describe, expect, it } from "vitest";
import { isAppUserAgent } from "./native-bridge";

describe("the app bridge", () => {
  it("knows the app's web view by its user agent", () => {
    expect(isAppUserAgent("Mozilla/5.0 (iPhone) AppleWebKit/605.1.15 StandardPhysicsApp/7")).toBe(true);
    expect(isAppUserAgent("Mozilla/5.0 (Macintosh) Safari/605.1.15")).toBe(false);
    expect(isAppUserAgent(null)).toBe(false);
  });
});
