import { describe, expect, it } from "vitest";
import { furnitureProgress } from "./furniture-refinement";

describe("furniture refinement progress", () => {
  it("shows nothing and keeps no spinner on a server without furniture refinement", () => {
    expect(furnitureProgress("unavailable")).toBe("hidden");
  });

  it("spins while a job is on its way, offers a retry after a failure and a count once done", () => {
    expect(furnitureProgress("not_started")).toBe("working");
    expect(furnitureProgress("running")).toBe("working");
    expect(furnitureProgress("failed")).toBe("failed");
    expect(furnitureProgress("done")).toBe("result");
  });
});
