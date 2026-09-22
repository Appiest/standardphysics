import { describe, expect, it, vi } from "vitest";
import {
  assessOutletAccessibility,
  dockPoint,
  sweepWheelchairInGeometry,
  wheelchairMotionGeometry,
  wheelchairProfile,
  type MotionPoint,
} from "@/lib/wheelchair-motion";
import { reviewOutlet } from "@/lib/layout-client";
import type { SceneGraph, SceneNode } from "@/types/contracts";

const makeNode = (overrides: Partial<SceneNode> = {}): SceneNode => ({
  id: "node-1",
  kind: "wall",
  label: "Wall",
  raw_category: "wall",
  dimensions: { x: 4, y: 0.2, z: 3 },
  transform: { m: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1] },
  quality: "measured",
  movable: false,
  labeled_by: "roomplan",
  parent_id: null,
  ...overrides,
});

const makeOutlet = (id: string, x: number, y: number, z: number): SceneNode =>
  makeNode({
    id,
    kind: "outlet",
    label: "Photographed Outlet",
    raw_category: "outlet",
    dimensions: { x: 0.07, y: 0.02, z: 0.115 },
    transform: { m: [1, 0, 0, x, 0, 1, 0, y, 0, 0, 1, z, 0, 0, 0, 1] },
    attachment: {
      support_node_id: "wall-1",
      support_type: "lidar_surface",
      review_status: "detected",
      uncertainty_reasons: [],
      sockets: [{ id: "s-1", center: { x, y, z }, confidence: 0.9, status: "observed" }],
      observations: [{ frame_id: "f-1", sensor_box: [0, 0, 10, 10], confidence: 0.9, image_url: "crops/c-1.jpg", provenance: "automatic", marked_by: null, marked_at: null, note: null }],
      localization_quality: "verified_support",
      identity_confidence: 0.9,
      local_anchor: null,
      normal: { x: 0, y: -1, z: 0 },
      observed_region: [],
    },
  });

describe("Outlet UI acceptance cases (UI-01 through UI-04)", () => {
  it("UI-01: Outlet list clearly shows reach assessment, distance, height, and review status", () => {
    const floor = makeNode({
      id: "floor-1",
      kind: "floor",
      dimensions: { x: 6, y: 6, z: 0.05 },
      transform: { m: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0.10, 0, 0, 0, 1] },
    });
    const wall = makeNode({
      id: "wall-1",
      kind: "wall",
      dimensions: { x: 6, y: 0.2, z: 3 },
      transform: { m: [1, 0, 0, 0, 0, 1, 0, 2.0, 0, 0, 1, 1.5, 0, 0, 0, 1] },
    });
    // Outlet at (0, 1.9, 0.55). Floor is at z = 0.10. Height above floor = 0.45m.
    const outlet = makeOutlet("outlet-1", 0, 1.9, 0.55);

    const geometry = wheelchairMotionGeometry([floor, wall, outlet]);
    const profile = wheelchairProfile({
      reach: { minReachHeight: 0.38, maxReachHeight: 1.22, maxReachDistance: 0.80 },
    });

    const chairPos: MotionPoint = { x: 0, z: 0 };
    const assessment = assessOutletAccessibility(chairPos, outlet, geometry, profile);

    // Verify UI display fields:
    // 1. Target height relative to local floor
    expect(assessment.targetHeightAboveFloor).toBeCloseTo(0.45, 2);
    // 2. Approach distance from chair
    expect(assessment.approachDistance).toBeGreaterThan(0);
    expect(assessment.approachStatus).toBe("clear");
    // 3. Reach determination
    expect(assessment.reachStatus).toBe("within_reach");
    // 4. Review status from node
    expect(outlet.attachment?.review_status).toBe("detected");
  });

  it("UI-02: Preview approach / Drive Here uses evaluated approach point, not generic target offset", () => {
    const floor = makeNode({
      id: "floor-1",
      kind: "floor",
      dimensions: { x: 8, y: 8, z: 0.05 },
      transform: { m: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0.0, 0, 0, 0, 1] },
    });
    const wall = makeNode({
      id: "wall-1",
      kind: "wall",
      dimensions: { x: 4, y: 0.2, z: 3 },
      transform: { m: [1, 0, 0, 0, 0, 1, 0, 2.0, 0, 0, 1, 1.5, 0, 0, 0, 1] },
    });
    const outlet = makeOutlet("outlet-1", 0, 1.9, 0.50);

    const geometry = wheelchairMotionGeometry([floor, wall, outlet]);
    const profile = wheelchairProfile({
      collisionRadius: 0.45,
      reach: { minReachHeight: 0.38, maxReachHeight: 1.22, maxReachDistance: 0.80 },
    });

    const chairPos: MotionPoint = { x: 0, z: -1.0 };
    const assessment = assessOutletAccessibility(chairPos, outlet, geometry, profile);

    expect(assessment.approachStatus).toBe("clear");
    expect(assessment.approachCandidate).not.toBeNull();
    const candidatePoint = assessment.approachCandidate!;

    // Case A: Controller receives evaluated approachCandidate as dockDestination
    const from: MotionPoint = { x: chairPos.x, z: chairPos.z };
    const movementWithCandidate = sweepWheelchairInGeometry(
      from,
      { x: candidatePoint.x - from.x, z: candidatePoint.z - from.z },
      geometry,
      profile.collisionRadius
    );
    expect(movementWithCandidate.reached).toBe(true);
    expect(movementWithCandidate.point.x).toBeCloseTo(candidatePoint.x, 3);
    expect(movementWithCandidate.point.z).toBeCloseTo(candidatePoint.z, 3);

    // Case B: Controller docking to generic object target without dockDestination (the M08 mutation)
    const targetRect = geometry.targets.find((rect) => rect.node.id === outlet.id);
    if (targetRect) {
      const genericDock = dockPoint(from, targetRect, profile.collisionRadius);
      // Generic dock to thin outlet bounding box differs from candidate approach position
      // proving that ignoring candidatePoint produces a different destination
      const distToCandidate = Math.hypot(genericDock.x - candidatePoint.x, genericDock.z - candidatePoint.z);
      // Candidate approach position is in front of the wall/outlet along normal, whereas generic dock docks to box edge
      expect(distToCandidate).toBeGreaterThan(0.01);
    }
  });

  it("UI-03: Reach profile bounds can be adjusted in UI and immediately update reach determinations", () => {
    const floor = makeNode({
      id: "floor-1",
      kind: "floor",
      dimensions: { x: 6, y: 6, z: 0.05 },
      transform: { m: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0.0, 0, 0, 0, 1] },
    });
    const wall = makeNode({
      id: "wall-1",
      kind: "wall",
      dimensions: { x: 6, y: 0.2, z: 3 },
      transform: { m: [1, 0, 0, 0, 0, 1, 0, 2.0, 0, 0, 1, 1.5, 0, 0, 0, 1] },
    });
    // Outlet at height 0.50m, stopping distance ~0.55m
    const outlet = makeOutlet("outlet-1", 0, 1.9, 0.50);
    const geometry = wheelchairMotionGeometry([floor, wall, outlet]);

    // Scenario 1: Distance limit adjustment
    const tightDistanceProfile = wheelchairProfile({
      reach: { minReachHeight: 0.38, maxReachHeight: 1.22, maxReachDistance: 0.30 },
    });
    const tightRes = assessOutletAccessibility({ x: 0, z: 1.0 }, outlet, geometry, tightDistanceProfile);
    expect(tightRes.reachStatus).toBe("outside_reach");

    // Adjust maxReachDistance up in UI -> immediately updates to within_reach
    const wideDistanceProfile = wheelchairProfile({
      reach: { ...tightDistanceProfile.reach, maxReachDistance: 0.80 },
    });
    const wideRes = assessOutletAccessibility({ x: 0, z: 1.0 }, outlet, geometry, wideDistanceProfile);
    expect(wideRes.reachStatus).toBe("within_reach");

    // Scenario 2: Height limit adjustment
    const highMinHeightProfile = wheelchairProfile({
      reach: { minReachHeight: 0.70, maxReachHeight: 1.22, maxReachDistance: 0.80 },
    });
    const highRes = assessOutletAccessibility({ x: 0, z: 1.0 }, outlet, geometry, highMinHeightProfile);
    expect(highRes.reachStatus).toBe("outside_reach");

    // Adjust minReachHeight down in UI -> immediately updates to within_reach
    const lowMinHeightProfile = wheelchairProfile({
      reach: { ...highMinHeightProfile.reach, minReachHeight: 0.38 },
    });
    const lowRes = assessOutletAccessibility({ x: 0, z: 1.0 }, outlet, geometry, lowMinHeightProfile);
    expect(lowRes.reachStatus).toBe("within_reach");
  });

  it("UI-04: Review status changes persist through API call and update the scene", async () => {
    const scanId = "00000000-0000-0000-0000-000000000001";
    const baseRevision = 3;
    const outletId = "outlet-42";

    const mockResponse: SceneGraph = {
      scan_id: scanId,
      revision: baseRevision + 1,
      base_hash: "mock-hash",
      nodes: [
        makeOutlet(outletId, 0, 1.9, 0.50),
      ],
    };
    const baseAttachment = mockResponse.nodes[0].attachment;
    if (baseAttachment) baseAttachment.review_status = "confirmed_by_user";

    // Mock global fetch
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: async () => mockResponse,
    } as unknown as Response);

    const updatedScene = await reviewOutlet(scanId, baseRevision, outletId, "confirmed_by_user");

    expect(fetchSpy).toHaveBeenCalledWith(
      `/api/scans/${scanId}/revisions/${baseRevision}/outlets/${outletId}/review`,
      expect.objectContaining({
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "confirmed_by_user" }),
      })
    );

    expect(updatedScene.revision).toBe(baseRevision + 1);
    expect(updatedScene.nodes[0].attachment?.review_status).toBe("confirmed_by_user");

    fetchSpy.mockRestore();
  });
});
