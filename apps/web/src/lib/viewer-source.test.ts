import { describe, expect, it } from "vitest";
import { viewerSourcePlan } from "./viewer-source";

describe("viewerSourcePlan", () => {
  it("does not apply an older photo build's stale shapes to a current reconstructed GLB", () => {
    expect(viewerSourcePlan({
      materialMode: "reconstructed",
      hasCleanGlb: true,
      hasPhotoBuild: true,
      staleNodeIds: ["reconstructed-node"],
    })).toEqual({ usePhotoBuild: false, staleNodeIds: [], materialMode: "reconstructed" });
  });

  it("uses stale-node fallback only while displaying the photo bake", () => {
    expect(viewerSourcePlan({
      materialMode: "captured",
      hasCleanGlb: true,
      hasPhotoBuild: true,
      staleNodeIds: ["changed-node"],
    })).toEqual({ usePhotoBuild: true, staleNodeIds: ["changed-node"], materialMode: "captured" });
  });

  it("keeps the scanned-room mode separate from the box texture build", () => {
    expect(viewerSourcePlan({
      materialMode: "scan",
      hasCleanGlb: true,
      hasPhotoBuild: true,
      staleNodeIds: ["changed-node"],
    })).toEqual({ usePhotoBuild: false, staleNodeIds: [], materialMode: "scan" });
  });
});
