import { afterEach, describe, expect, it, vi } from "vitest";
import { getSimulation } from "./simulation-client";

describe("getSimulation", () => {
  afterEach(() => vi.restoreAllMocks());

  it("bypasses browser caches while polling live progress", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ completed: 1 }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));

    await getSimulation("scan-1", 4);

    expect(fetch).toHaveBeenCalledWith("/api/scans/scan-1/simulations?revision=4", { cache: "no-store" });
  });
});
