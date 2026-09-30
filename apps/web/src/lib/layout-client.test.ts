import { afterEach, describe, expect, it, vi } from "vitest";
import { checkLayout, LAYOUT_CHECK_MS } from "./layout-client";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("a plan check", () => {
  it("gives up when the server never answers, so the plan stops saying it is checking", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", (_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
      init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    }));
    const check = checkLayout("scan", 0, 1, []);
    const outcome = expect(check).rejects.toThrow();
    await vi.advanceTimersByTimeAsync(LAYOUT_CHECK_MS);
    await outcome;
  });
});
