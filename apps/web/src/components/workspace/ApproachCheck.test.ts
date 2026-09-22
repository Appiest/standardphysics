import { describe, expect, it, vi } from "vitest";
import { reachLine } from "./ApproachCheck";
import { evaluateApproach } from "@/lib/approach-client";
import type { ReachReport } from "@/types/contracts";

const reach = (overrides: Partial<ReachReport> = {}): ReachReport => ({
  occupant_title: "Manual wheelchair",
  target_height_inches: 42,
  vertical_status: "within_vertical_reach",
  horizontal_distance_inches: null,
  horizontal_reach_inches: null,
  horizontal_reach_provenance: null,
  horizontal_status: "unmeasured",
  ...overrides,
});

describe("approach reach lines", () => {
  it("names the unmeasured horizontal reach instead of inventing one", () => {
    const line = reachLine(reach());
    expect(line).toContain("Manual wheelchair");
    expect(line).toContain("Height is within reach");
    expect(line).not.toContain("Side reach");
  });

  it("quotes a person-provided reach with who provided it", () => {
    const line = reachLine(reach({
      horizontal_status: "within_horizontal_reach",
      horizontal_reach_inches: 18,
      horizontal_reach_provenance: "owner measured sideways grasp",
    }));
    expect(line).toContain("18 in, owner measured sideways grasp");
    expect(line).toContain("Side reach: within what you measured");
  });

  it("labels a block as farther than the measured reach", () => {
    const line = reachLine(reach({ horizontal_status: "exceeded_horizontal_reach" }));
    expect(line).toContain("Side reach: farther than what you measured");
  });
});

describe("approach client", () => {
  it("posts the target to the frozen endpoint and returns the report", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ target_id: "n", status: "needs_verification", reasons: [], reaches: [], unverified: [] }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const report = await evaluateApproach("scan-1", 4, { target_node_id: "node-9", occupant_profile: "manual-wheelchair", horizontal_reach_inches: null, horizontal_reach_provenance: null, approach_stop: null });

    expect(report.status).toBe("needs_verification");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/scans/scan-1/revisions/4/approach",
      expect.objectContaining({ method: "POST" }),
    );
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body.target_node_id).toBe("node-9");
    vi.unstubAllGlobals();
  });

  it("surfaces a stale conflict as an error body", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: "stale" }), { status: 409 })));
    await expect(evaluateApproach("s", 1, { target_node_id: "n", occupant_profile: "manual-wheelchair", horizontal_reach_inches: null, horizontal_reach_provenance: null, approach_stop: null })).rejects.toEqual({ error: "stale" });
    vi.unstubAllGlobals();
  });
});
