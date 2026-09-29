import { describe, expect, it } from "vitest";
import type { SceneNode } from "@/types/contracts";

import { assessOutletAccessibility } from "./outlet-accessibility";
import { wheelchairMotionGeometry } from "./wheelchair-geometry";
import { sweepWheelchairInGeometry } from "./wheelchair-navigation";
import { wheelchairProfile } from "./wheelchair-profile";
import { node } from "./wheelchair-test-nodes";

describe("assessOutletAccessibility", () => {
  const floor = node({ id: "floor", kind: "floor", dimensions: { x: 10, y: 10, z: 0.05 }, y: 0 });
  const wall = node({ id: "wall", kind: "wall", dimensions: { x: 6, y: 0.2, z: 3 }, y: 2 });
  const outlet: SceneNode = {
    id: "outlet-1",
    kind: "outlet",
    label: "Outlet 1",
    raw_category: "outlet",
    dimensions: { x: 0.12, y: 0.03, z: 0.12 },
    transform: { m: [1, 0, 0, 0, 0, 1, 0, 1.9, 0, 0, 1, 0.45, 0, 0, 0, 1] },
    quality: "measured",
    movable: false,
    labeled_by: "discovery",
    parent_id: "wall",
    relation: "mounted_on",
    attachment: {
      support_node_id: "wall",
      support_type: "lidar_surface",
      normal: { x: 0, y: -1, z: 0 },
      local_anchor: null,
      observed_region: [],
      localization_quality: "verified_support",
      identity_confidence: 0.95,
      review_status: "detected",
      uncertainty_reasons: [],
      sockets: [],
      observations: [],
    },
  };

  it("returns clear approach and within_reach when path is free and reach profile matches", () => {
    const geometry = wheelchairMotionGeometry([floor, wall, outlet]);
    const profile = wheelchairProfile({
      collisionRadius: 0.35,
      reach: { maxReachDistance: 0.75, minReachHeight: 0.38, maxReachHeight: 1.22 },
    });
    const currentPos = { x: 0, z: 0 };
    const res = assessOutletAccessibility(currentPos, outlet, geometry, profile);
    expect(res.approachStatus).toBe("clear");
    expect(res.reachStatus).toBe("within_reach");
    expect(res.targetHeightAboveFloor).toBeCloseTo(0.45, 1);
    expect(res.disclaimers.length).toBeGreaterThan(0);
  });

  it("returns blocked when an obstacle blocks route to the outlet", () => {
    // Barrier between user and outlet
    const barrier = node({ id: "barrier", kind: "object", dimensions: { x: 8, y: 0.5, z: 2 }, y: 1 });
    const geometry = wheelchairMotionGeometry([floor, wall, barrier, outlet]);
    const profile = wheelchairProfile({ collisionRadius: 0.35 });
    const res = assessOutletAccessibility({ x: 0, z: 0 }, outlet, geometry, profile);
    expect(res.approachStatus).toBe("blocked");
    expect(res.unresolvedReasons.some((r) => r.includes("obstructed") || r.includes("No valid"))).toBe(true);
  });

  it("returns needs_verification when personalized reach profile is missing", () => {
    const geometry = wheelchairMotionGeometry([floor, wall, outlet]);
    const profile = wheelchairProfile({ reach: null });
    const res = assessOutletAccessibility({ x: 0, z: 0 }, outlet, geometry, profile);
    expect(res.reachStatus).toBe("needs_verification");
    expect(res.unresolvedReasons.some((r) => r.includes("reach profile"))).toBe(true);
  });

  it("differentiates two reach profiles with different reach limits", () => {
    const geometry = wheelchairMotionGeometry([floor, wall, outlet]);
    const shortReach = wheelchairProfile({
      collisionRadius: 0.35,
      reach: { maxReachDistance: 0.40, minReachHeight: 0.38, maxReachHeight: 1.22 },
    });
    const longReach = wheelchairProfile({
      collisionRadius: 0.35,
      reach: { maxReachDistance: 0.85, minReachHeight: 0.38, maxReachHeight: 1.22 },
    });

    const resShort = assessOutletAccessibility({ x: 0, z: 0 }, outlet, geometry, shortReach);
    const resLong = assessOutletAccessibility({ x: 0, z: 0 }, outlet, geometry, longReach);
    expect(resShort.reachStatus).toBe("outside_reach");
    expect(resLong.reachStatus).toBe("within_reach");
  });

  it("blocks wheelchair navigation through a narrow passage narrower than chair footprint", () => {
    // Two wall segments with a gap of 0.60 m between them (X from -5 to -0.3, and X from 0.3 to 5)
    const leftWall = node({ id: "left-wall", kind: "wall", dimensions: { x: 4.7, y: 0.2, z: 3 }, x: -2.65, y: 2 });
    const rightWall = node({ id: "right-wall", kind: "wall", dimensions: { x: 4.7, y: 0.2, z: 3 }, x: 2.65, y: 2 });
    const narrowGeometry = wheelchairMotionGeometry([floor, leftWall, rightWall]);
    // Wheelchair radius 0.40m (diameter 0.80m > 0.60m gap)
    const sweep = sweepWheelchairInGeometry({ x: 0, z: 0 }, { x: 0, z: -4 }, narrowGeometry, 0.40);
    expect(sweep.reached).toBe(false);
  });

  it("blocks approach to an outlet on the far side of a wall", () => {
    // Dividing wall that completely separates near side from far side
    const dividingWall = node({ id: "dividing-wall", kind: "wall", dimensions: { x: 12, y: 0.2, z: 3 }, y: 2 });
    // Outlet is at y=2.1 with normal pointing away (+y in RoomPlan -> -z in Three.js)
    const farSideOutlet: SceneNode = {
      ...outlet,
      id: "far-side-outlet",
      transform: { m: [1, 0, 0, 0, 0, 1, 0, 2.1, 0, 0, 1, 0.45, 0, 0, 0, 1] },
      attachment: {
        ...outlet.attachment!,
        normal: { x: 0, y: 1, z: 0 }, // Normal points into +y (far side)
      },
    };
    const geometry = wheelchairMotionGeometry([floor, dividingWall, farSideOutlet]);
    const profile = wheelchairProfile({ collisionRadius: 0.35 });
    // Chair is at y=0 (near side). Moving to the far side requires crossing the solid wall.
    const res = assessOutletAccessibility({ x: 0, z: 0 }, farSideOutlet, geometry, profile);
    expect(res.approachStatus).toBe("blocked");
    expect(res.unresolvedReasons.some((r) => r.includes("obstructed") || r.includes("No valid"))).toBe(true);
  });

  it("computes target height relative to local finished floor, not world origin", () => {
    // Raised platform floor at z = 0.40m
    const raisedFloor = node({
      id: "raised-floor",
      kind: "floor",
      dimensions: { x: 10, y: 10, z: 0.05 },
      transform: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0.40, 0, 0, 0, 1],
    });
    // Outlet at world z = 0.85m
    const elevatedOutlet: SceneNode = {
      ...outlet,
      id: "elevated-outlet",
      transform: { m: [1, 0, 0, 0, 0, 1, 0, 1.9, 0, 0, 1, 0.85, 0, 0, 0, 1] },
    };
    const geometry = wheelchairMotionGeometry([raisedFloor, wall, elevatedOutlet]);
    const profile = wheelchairProfile({
      collisionRadius: 0.35,
      reach: { maxReachDistance: 0.80, minReachHeight: 0.38, maxReachHeight: 1.22 },
    });
    const res = assessOutletAccessibility({ x: 0, z: 0 }, elevatedOutlet, geometry, profile);
    // Target height above floor should be 0.85 - 0.40 = 0.45m, NOT 0.85m!
    expect(res.targetHeightAboveFloor).toBeCloseTo(0.45, 2);
    expect(res.reachStatus).toBe("within_reach");
  });

  it("returns outside_reach when outlet is behind deep furniture", () => {
    // Deep desk (1.2m deep along Y) placed in front of wall outlet at Y=1.9
    const deepDesk = node({ id: "deep-desk", kind: "object", dimensions: { x: 1.5, y: 1.2, z: 0.75 }, x: 0, y: 1.3 });
    const geometry = wheelchairMotionGeometry([floor, wall, deepDesk, outlet]);
    const profile = wheelchairProfile({
      collisionRadius: 0.35,
      reach: { maxReachDistance: 0.50, minReachHeight: 0.38, maxReachHeight: 1.22 },
    });
    const res = assessOutletAccessibility({ x: 0, z: 0 }, outlet, geometry, profile);
    // Chair can approach the room, but stopping position is stopped by desk, so reach to wall outlet exceeds 0.50m
    expect(res.reachStatus).toBe("outside_reach");
    expect(res.unresolvedReasons.some((r) => r.includes("exceeds maximum modeled reach") || r.includes("obstructed"))).toBe(true);
  });

  it("invalidates approach from clear to blocked after an obstacle is introduced", () => {
    const profile = wheelchairProfile({ collisionRadius: 0.35 });
    const initialGeometry = wheelchairMotionGeometry([floor, wall, outlet]);
    const initialRes = assessOutletAccessibility({ x: 0, z: 0 }, outlet, initialGeometry, profile);
    expect(initialRes.approachStatus).toBe("clear");

    // Move a barrier directly in the path between start and outlet
    const barrier = node({ id: "barrier", kind: "object", dimensions: { x: 8, y: 0.5, z: 2 }, y: 1.0 });
    const updatedGeometry = wheelchairMotionGeometry([floor, wall, barrier, outlet]);
    const updatedRes = assessOutletAccessibility({ x: 0, z: 0 }, outlet, updatedGeometry, profile);
    expect(updatedRes.approachStatus).toBe("blocked");
  });

  // REACH-01 through REACH-06
  it("REACH-01: missing profile, missing vertical limits, or missing hand reference yields needs_verification", () => {
    const geometry = wheelchairMotionGeometry([floor, wall, outlet]);

    // Missing profile
    const noProfile = assessOutletAccessibility({ x: 0, z: 0 }, outlet, geometry, wheelchairProfile({ reach: null }));
    expect(noProfile.reachStatus).toBe("needs_verification");
    expect(noProfile.unresolvedReasons.some((r) => r.includes("Personalized reach profile not supplied"))).toBe(true);

    // Missing vertical limits (no implicit ADA default)
    const noVerticalLimits = assessOutletAccessibility(
      { x: 0, z: 0 },
      outlet,
      geometry,
      wheelchairProfile({ reach: { maxReachDistance: 0.80 } }),
    );
    expect(noVerticalLimits.reachStatus).toBe("needs_verification");
    expect(noVerticalLimits.unresolvedReasons.some((r) => r.includes("Vertical reach limits"))).toBe(true);
  });

  it("REACH-02: NaN, Inf, negative reach, inverted height bounds rejected without within_reach", () => {
    const geometry = wheelchairMotionGeometry([floor, wall, outlet]);

    const nanProfile = wheelchairProfile({
      reach: { maxReachDistance: Number.NaN, minReachHeight: 0.38, maxReachHeight: 1.22 },
    });
    const infProfile = wheelchairProfile({
      reach: { maxReachDistance: Number.POSITIVE_INFINITY, minReachHeight: 0.38, maxReachHeight: 1.22 },
    });
    const negProfile = wheelchairProfile({
      reach: { maxReachDistance: -0.5, minReachHeight: 0.38, maxReachHeight: 1.22 },
    });
    const invertedProfile = wheelchairProfile({
      reach: { maxReachDistance: 0.8, minReachHeight: 1.22, maxReachHeight: 0.38 },
    });

    for (const prof of [nanProfile, infProfile, negProfile, invertedProfile]) {
      const res = assessOutletAccessibility({ x: 0, z: 0 }, outlet, geometry, prof);
      expect(res.reachStatus).toBe("needs_verification");
      expect(res.unresolvedReasons.some((r) => r.includes("Invalid reach profile"))).toBe(true);
    }
  });

  it("REACH-03: missing local floor, missing normal, or unresolved socket yields needs_verification without world-zero fallback", () => {
    // Geometry with floor only around (10, 10), far away from outlet at (0, 1.9)
    const detachedFloor = node({ id: "detached-floor", kind: "floor", dimensions: { x: 2, y: 2, z: 0.05 }, x: 10, y: 10 });
    const noFloorGeometry = wheelchairMotionGeometry([detachedFloor, wall, outlet]);
    const validProfile = wheelchairProfile({
      reach: { maxReachDistance: 0.80, minReachHeight: 0.38, maxReachHeight: 1.22 },
    });

    const resNoFloor = assessOutletAccessibility({ x: 10, z: 10 }, outlet, noFloorGeometry, validProfile);
    expect(resNoFloor.targetHeightAboveFloor).toBeNull();
    expect(resNoFloor.reachStatus).toBe("needs_verification");
    expect(resNoFloor.unresolvedReasons.some((r) => r.includes("No modeled floor under target"))).toBe(true);

    // Missing support normal
    const noNormalOutlet: SceneNode = {
      ...outlet,
      attachment: { ...outlet.attachment!, normal: undefined as never },
    };
    const resNoNormal = assessOutletAccessibility({ x: 0, z: 0 }, noNormalOutlet, wheelchairMotionGeometry([floor, wall, noNormalOutlet]), validProfile);
    expect(resNoNormal.reachStatus).toBe("needs_verification");

    // Unresolved socket target
    const unresolvedSocketOutlet: SceneNode = {
      ...outlet,
      attachment: { ...outlet.attachment!, uncertainty_reasons: ["unresolved socket target"] },
    };
    const resUnresolvedSocket = assessOutletAccessibility({ x: 0, z: 0 }, unresolvedSocketOutlet, wheelchairMotionGeometry([floor, wall, unresolvedSocketOutlet]), validProfile);
    expect(resUnresolvedSocket.reachStatus).toBe("needs_verification");
  });

  it("REACH-04: identical geometry produces within_reach for 0.80m limit and outside_reach for 0.40m limit", () => {
    const geometry = wheelchairMotionGeometry([floor, wall, outlet]);
    // Chair at (0, 1.30), target at (0, 1.90) -> distance = 0.60m
    const pos = { x: 0, z: -1.30 };
    const reach80 = wheelchairProfile({
      collisionRadius: 0.35,
      reach: { maxReachDistance: 0.80, minReachHeight: 0.38, maxReachHeight: 1.22 },
    });
    const reach40 = wheelchairProfile({
      collisionRadius: 0.35,
      reach: { maxReachDistance: 0.40, minReachHeight: 0.38, maxReachHeight: 1.22 },
    });

    const res80 = assessOutletAccessibility(pos, outlet, geometry, reach80);
    const res40 = assessOutletAccessibility(pos, outlet, geometry, reach40);
    expect(res80.reachStatus).toBe("within_reach");
    expect(res40.reachStatus).toBe("outside_reach");
  });

  it("REACH-05: distance/height intervals crossing thresholds yield needs_verification without arbitrary 90% rule", () => {
    const geometry = wheelchairMotionGeometry([floor, wall, outlet]);

    // Distance interval [0.55, 0.65]m crosses maxReachDistance limit 0.60m
    const thresholdCrossingDistance = wheelchairProfile({
      collisionRadius: 0.35,
      reach: {
        maxReachDistance: 0.60,
        minReachHeight: 0.38,
        maxReachHeight: 1.22,
        distanceInterval: [0.55, 0.65],
      },
    });
    const resDist = assessOutletAccessibility({ x: 0, z: 0 }, outlet, geometry, thresholdCrossingDistance);
    expect(resDist.reachStatus).toBe("needs_verification");
    expect(resDist.unresolvedReasons.some((r) => r.includes("interval crosses maximum reach threshold"))).toBe(true);

    // Height interval [0.35, 0.45]m crosses minReachHeight limit 0.38m
    const thresholdCrossingHeight = wheelchairProfile({
      collisionRadius: 0.35,
      reach: {
        maxReachDistance: 0.80,
        minReachHeight: 0.38,
        maxReachHeight: 1.22,
        heightInterval: [0.35, 0.45],
      },
    });
    const resHeight = assessOutletAccessibility({ x: 0, z: 0 }, outlet, geometry, thresholdCrossingHeight);
    expect(resHeight.reachStatus).toBe("needs_verification");
    expect(resHeight.unresolvedReasons.some((r) => r.includes("interval crosses vertical reach threshold"))).toBe(true);

    // No arbitrary 90% rule: reachDistance 0.65m with maxReachDistance 0.70m (93% of limit) with exact bounds is within_reach
    const exactWithinReach = wheelchairProfile({
      collisionRadius: 0.35,
      reach: { maxReachDistance: 0.70, minReachHeight: 0.38, maxReachHeight: 1.22 },
    });
    const resExact = assessOutletAccessibility({ x: 0, z: 0 }, outlet, geometry, exactWithinReach);
    expect(resExact.reachStatus).toBe("within_reach");
  });

  it("REACH-06: floor elevation 1.0m and socket elevation 1.5m yields target height above floor 0.5m", () => {
    const elevatedFloor = node({
      id: "elevated-floor",
      kind: "floor",
      dimensions: { x: 10, y: 10, z: 0.05 },
      transform: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 1.0, 0, 0, 0, 1],
    });
    const elevatedSocket: SceneNode = {
      ...outlet,
      transform: { m: [1, 0, 0, 0, 0, 1, 0, 1.9, 0, 0, 1, 1.5, 0, 0, 0, 1] },
    };
    const geometry = wheelchairMotionGeometry([elevatedFloor, wall, elevatedSocket]);
    const profile = wheelchairProfile({
      reach: { maxReachDistance: 0.80, minReachHeight: 0.38, maxReachHeight: 1.22 },
    });
    const res = assessOutletAccessibility({ x: 0, z: 0 }, elevatedSocket, geometry, profile);
    expect(res.targetHeightAboveFloor).toBeCloseTo(0.5, 3);
  });

  // ROUTE-01 through ROUTE-06
  it("ROUTE-01: finds valid detour when direct route is blocked by wall", () => {
    // Wall placed between start (0, 0) and target stopping point (0, -1.5)
    // Wall spans x = [-2, 2] at y = 0.8. Opening is on the side x > 2.2.
    const blockingWall = node({ id: "blocking-wall", kind: "wall", dimensions: { x: 4, y: 0.2, z: 3 }, x: 0, y: 0.8 });
    const detourFloor = node({ id: "large-floor", kind: "floor", dimensions: { x: 12, y: 12, z: 0.05 } });
    const geometry = wheelchairMotionGeometry([detourFloor, blockingWall, outlet]);
    const profile = wheelchairProfile({
      collisionRadius: 0.35,
      reach: { maxReachDistance: 0.80, minReachHeight: 0.38, maxReachHeight: 1.22 },
    });

    const res = assessOutletAccessibility({ x: 0, z: 0 }, outlet, geometry, profile);
    expect(res.approachStatus).toBe("clear");
    // Detour distance must be greater than straight line distance
    expect(res.approachDistance).toBeGreaterThan(1.5);
  });

  it("ROUTE-02: floor gap, no floor, and narrow passage prevent clear route with distinguished causes", () => {
    // 1. Floor gap: start floor separated from outlet floor by 2m void
    const startFloor = node({ id: "start-floor", kind: "floor", dimensions: { x: 2, y: 2, z: 0.05 }, x: 0, y: 0 });
    const outletFloor = node({ id: "outlet-floor", kind: "floor", dimensions: { x: 2, y: 2, z: 0.05 }, x: 0, y: 4 });
    const gapGeometry = wheelchairMotionGeometry([startFloor, outletFloor, outlet]);
    const profile = wheelchairProfile({ collisionRadius: 0.35 });

    const resGap = assessOutletAccessibility({ x: 0, z: 0 }, outlet, gapGeometry, profile);
    expect(resGap.approachStatus).toBe("blocked");

    // 2. Narrow passage (0.60m gap with chair diameter 0.80m)
    const leftWall = node({ id: "left-wall", kind: "wall", dimensions: { x: 4.7, y: 0.2, z: 3 }, x: -2.65, y: 1 });
    const rightWall = node({ id: "right-wall", kind: "wall", dimensions: { x: 4.7, y: 0.2, z: 3 }, x: 2.65, y: 1 });
    const narrowGeometry = wheelchairMotionGeometry([floor, leftWall, rightWall, outlet]);
    const resNarrow = assessOutletAccessibility({ x: 0, z: 0 }, outlet, narrowGeometry, profile);
    expect(resNarrow.approachStatus).toBe("blocked");
  });

  it("ROUTE-03: swept collision check prevents tunneling through thin obstacle", () => {
    // Thin wall 0.05m thick between (0,0) and (0, -2)
    const thinWall = node({ id: "thin-wall", kind: "wall", dimensions: { x: 6, y: 0.05, z: 3 }, y: 1 });
    const geometry = wheelchairMotionGeometry([floor, thinWall]);
    const sweep = sweepWheelchairInGeometry({ x: 0, z: 0 }, { x: 0, z: -2 }, geometry, 0.35);
    expect(sweep.reached).toBe(false);
  });

  it("ROUTE-04: reach obstruction detected between chair and target even though neither endpoint touches barrier", () => {
    // Target at (0, 0), chair near (0, 0.65)
    // Barrier at x=[-2, 2] z=[0.13, 0.17] (in Three.js motion plane: center z=0.15, so y=-0.15 in scene coords)
    const barrier = node({
      id: "reach-barrier",
      kind: "object",
      dimensions: { x: 4.0, y: 0.04, z: 1.0 },
      x: 0,
      y: -0.15,
    });
    const zeroOutlet: SceneNode = {
      ...outlet,
      transform: { m: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0.45, 0, 0, 0, 1] },
    };
    const geometry = wheelchairMotionGeometry([floor, barrier, zeroOutlet]);
    const profile = wheelchairProfile({
      collisionRadius: 0.20,
      reach: { maxReachDistance: 0.80, minReachHeight: 0.38, maxReachHeight: 1.22 },
    });

    // Chair at z=0.65 in Three.js coordinates
    const res = assessOutletAccessibility({ x: 0, z: 0.65 }, zeroOutlet, geometry, profile);
    // Must NOT be within_reach because barrier is between endpoints
    expect(res.reachStatus).not.toBe("within_reach");
    expect(res.reachStatus).toBe("outside_reach");
    expect(res.unresolvedReasons.some((r) => r.includes("obstructed"))).toBe(true);
  });

  it("ROUTE-05: cannot reach through parent cabinet or far-side wall", () => {
    // 1. Far-side wall socket: outlet is at y=2.1 with normal pointing into wall (+y)
    const dividingWall = node({ id: "dividing-wall", kind: "wall", dimensions: { x: 12, y: 0.2, z: 3 }, y: 2 });
    const farSideOutlet: SceneNode = {
      ...outlet,
      id: "far-side-outlet",
      transform: { m: [1, 0, 0, 0, 0, 1, 0, 2.1, 0, 0, 1, 0.45, 0, 0, 0, 1] },
      attachment: {
        ...outlet.attachment!,
        normal: { x: 0, y: 1, z: 0 },
      },
    };
    const geometry = wheelchairMotionGeometry([floor, dividingWall, farSideOutlet]);
    const profile = wheelchairProfile({
      collisionRadius: 0.35,
      reach: { maxReachDistance: 0.80, minReachHeight: 0.38, maxReachHeight: 1.22 },
    });
    const resFarSide = assessOutletAccessibility({ x: 0, z: 0 }, farSideOutlet, geometry, profile);
    expect(resFarSide.reachStatus).not.toBe("within_reach");

    // 2. Parent cabinet between hand and socket
    const cabinet = node({ id: "cabinet", kind: "object", dimensions: { x: 1.2, y: 0.8, z: 0.9 }, x: 0, y: 1.5 });
    const cabinetOutlet: SceneNode = {
      ...outlet,
      parent_id: "cabinet",
      transform: { m: [1, 0, 0, 0, 0, 1, 0, 1.9, 0, 0, 1, 0.45, 0, 0, 0, 1] },
    };
    const cabinetGeometry = wheelchairMotionGeometry([floor, cabinet, cabinetOutlet]);
    const resCabinet = assessOutletAccessibility({ x: 0, z: -0.5 }, cabinetOutlet, cabinetGeometry, profile);
    expect(resCabinet.reachStatus).not.toBe("within_reach");
  });

  it("ROUTE-06: unknown chair position produces needs_verification without invented origin", () => {
    const geometry = wheelchairMotionGeometry([floor, wall, outlet]);
    const profile = wheelchairProfile({
      reach: { maxReachDistance: 0.80, minReachHeight: 0.38, maxReachHeight: 1.22 },
    });

    const res = assessOutletAccessibility(null, outlet, geometry, profile);
    expect(res.approachStatus).toBe("needs_verification");
    expect(res.approachDistance).toBeNull();
    expect(res.reachStatus).toBe("needs_verification");
    expect(res.unresolvedReasons.some((r) => r.includes("position is unknown"))).toBe(true);
  });
});
