import type { SceneNode } from "@/types/contracts";

import { add, length, normalize, scale, subtract, type MotionPoint } from "./motion-vector";
import { containsPoint, segmentIntersectsRect, type WheelchairMotionGeometry } from "./wheelchair-geometry";
import { canOccupyWheelchair, findWheelchairPath } from "./wheelchair-navigation";
import type { ReachProfile, WheelchairProfile } from "./wheelchair-profile";

export type ApproachStatus = "clear" | "blocked" | "needs_verification";
export type ReachStatus = "within_reach" | "outside_reach" | "needs_verification";

export type OutletAccessibilityAssessment = {
  approachStatus: ApproachStatus;
  approachCandidate: MotionPoint | null;
  approachDistance: number | null;
  approachHeadingDeg: number | null;
  reachStatus: ReachStatus;
  targetHeightAboveFloor: number | null;
  reachDistance: number | null;
  unresolvedReasons: string[];
  disclaimers: string[];
};

type ApproachCandidate = { point: MotionPoint; distance: number; heading: number };
type ApproachSelection = {
  status: ApproachStatus;
  candidate: MotionPoint | null;
  distance: number | null;
  heading: number | null;
};
type ValidReachProfile = ReachProfile & {
  maxReachDistance: number;
  minReachHeight: number;
  maxReachHeight: number;
};

const OUTLET_DISCLAIMERS = [
  "Electrical power, circuit live status, voltage, and socket condition are not established.",
  "Plug insertion force, dexterity, and grip requirements are not established.",
  "ADA compliance is not established.",
  "Passing modeled reach is not proof of grasp, plug insertion, arm motion, strength or safe independent use.",
];

function outletHeightAboveFloor(
  target: MotionPoint,
  height: number,
  geometry: WheelchairMotionGeometry,
  reasons: string[],
): number | null {
  const floor = geometry.floors.find((candidate) => containsPoint(candidate, target, 0.05));
  if (floor) return Math.max(0, height - floor.node.transform.m[11]);
  reasons.push("No modeled floor under target; local floor elevation unknown.");
  return null;
}

function outletApproachCandidates(
  target: MotionPoint,
  normal: MotionPoint,
  current: MotionPoint | null | undefined,
  geometry: WheelchairMotionGeometry,
  radius: number,
): ApproachCandidate[] {
  const candidates: ApproachCandidate[] = [];
  const lateralAxis: MotionPoint = { x: -normal.z, z: normal.x };
  for (let distance = radius + 0.10; distance <= 0.70; distance += 0.10) {
    for (let lateral = -0.30; lateral <= 0.30; lateral += 0.10) {
      const point = add(target, add(scale(normal, distance), scale(lateralAxis, lateral)));
      if (!canOccupyWheelchair(point, geometry, radius)) continue;
      const toOutlet = subtract(target, point);
      const heading = (Math.atan2(toOutlet.x, toOutlet.z) * 180) / Math.PI;
      const startDistance = current ? length(subtract(current, point)) : 0;
      candidates.push({ point, distance: startDistance, heading });
    }
  }
  candidates.sort((left, right) => left.distance - right.distance);
  return candidates;
}

function outletReachObstructed(
  from: MotionPoint,
  target: MotionPoint,
  outletId: string,
  geometry: WheelchairMotionGeometry,
): boolean {
  return geometry.obstacles.some((obstacle) => {
    if (obstacle.node.id === outletId) return false;
    return segmentIntersectsRect(from, target, obstacle);
  });
}

function selectOutletApproach(
  current: MotionPoint | null | undefined,
  candidates: ApproachCandidate[],
  target: MotionPoint,
  outletId: string,
  geometry: WheelchairMotionGeometry,
  radius: number,
  reasons: string[],
): ApproachSelection {
  if (!current) return { status: "needs_verification", candidate: null, distance: null, heading: null };
  for (const candidate of candidates) {
    const path = findWheelchairPath(current, candidate.point, geometry, radius);
    if (!path.reached) continue;
    if (outletReachObstructed(candidate.point, target, outletId, geometry)) continue;
    return { status: "clear", candidate: candidate.point, distance: path.distance, heading: candidate.heading };
  }
  const nearest = candidates[0];
  if (nearest) {
    reasons.push("Candidate stopping position exists on floor, but route from current position is obstructed.");
    return { status: "blocked", candidate: nearest.point, distance: nearest.distance, heading: nearest.heading };
  }
  reasons.push("No valid modeled floor position found within approach range.");
  return { status: "blocked", candidate: null, distance: null, heading: null };
}

function validVerticalReach(reach: ReachProfile): boolean {
  return Number.isFinite(reach.minReachHeight)
    && Number.isFinite(reach.maxReachHeight)
    && reach.minReachHeight! >= 0
    && reach.minReachHeight! < reach.maxReachHeight!;
}

function validatedReach(profile: WheelchairProfile, reasons: string[]): ValidReachProfile | null {
  const reach = profile.reach;
  if (!reach) {
    reasons.push("Personalized reach profile not supplied; reach cannot be established.");
    return null;
  }
  if (reach.maxReachDistance === undefined) {
    reasons.push("Maximum reach distance not supplied; reach cannot be established.");
    return null;
  }
  if (reach.minReachHeight === undefined || reach.maxReachHeight === undefined) {
    reasons.push("Vertical reach limits (minReachHeight, maxReachHeight) not supplied; implicit ADA dimensions not assumed.");
    return null;
  }
  if (!Number.isFinite(reach.maxReachDistance) || reach.maxReachDistance <= 0) {
    reasons.push("Invalid reach profile: maxReachDistance must be a finite positive number.");
    return null;
  }
  if (!validVerticalReach(reach)) {
    reasons.push("Invalid reach profile: vertical limits must be finite numbers with minReachHeight < maxReachHeight.");
    return null;
  }
  return reach as ValidReachProfile;
}

function distanceReachStatus(distance: number, reach: ValidReachProfile, reasons: string[]): ReachStatus | null {
  const interval = reach.distanceInterval ?? [distance, distance];
  if (interval[0] <= reach.maxReachDistance && interval[1] > reach.maxReachDistance) {
    reasons.push("Reach distance interval crosses maximum reach threshold; needs in-person verification.");
    return "needs_verification";
  }
  if (interval[0] > reach.maxReachDistance) {
    reasons.push(`Estimated reach distance (${distance.toFixed(2)}m) exceeds maximum modeled reach (${reach.maxReachDistance.toFixed(2)}m).`);
    return "outside_reach";
  }
  return null;
}

function crossesVerticalReach(interval: [number, number], reach: ValidReachProfile): boolean {
  return (interval[0] < reach.minReachHeight && interval[1] >= reach.minReachHeight)
    || (interval[0] <= reach.maxReachHeight && interval[1] > reach.maxReachHeight);
}

function heightReachStatus(height: number, reach: ValidReachProfile, reasons: string[]): ReachStatus | null {
  const interval = reach.heightInterval ?? [height, height];
  if (crossesVerticalReach(interval, reach)) {
    reasons.push("Target height interval crosses vertical reach threshold; needs in-person verification.");
    return "needs_verification";
  }
  if (height < reach.minReachHeight || height > reach.maxReachHeight) {
    reasons.push(`Target height (${height.toFixed(2)}m) is outside vertical reach envelope (${reach.minReachHeight.toFixed(2)}m - ${reach.maxReachHeight.toFixed(2)}m).`);
    return "outside_reach";
  }
  return null;
}

function resolvedReachStatus(
  reach: ValidReachProfile,
  height: number,
  distance: number,
  from: MotionPoint | null | undefined,
  target: MotionPoint,
  outletId: string,
  geometry: WheelchairMotionGeometry,
  reasons: string[],
): ReachStatus {
  if (from && outletReachObstructed(from, target, outletId, geometry)) {
    reasons.push("Reach path from stopping position to outlet is obstructed by an intervening obstacle.");
    return "outside_reach";
  }
  return distanceReachStatus(distance, reach, reasons) ?? heightReachStatus(height, reach, reasons) ?? "within_reach";
}

function outletReachStatus(
  profile: WheelchairProfile,
  height: number | null,
  distance: number | null,
  from: MotionPoint | null | undefined,
  target: MotionPoint,
  outletId: string,
  geometry: WheelchairMotionGeometry,
  hasNormal: boolean,
  unresolvedSocket: boolean,
  reasons: string[],
): ReachStatus {
  const reach = validatedReach(profile, reasons);
  if (!reach) return "needs_verification";
  if (height === null) {
    reasons.push("Target height above floor is unknown; reach cannot be established.");
    return "needs_verification";
  }
  if (!hasNormal || unresolvedSocket || distance === null) return "needs_verification";
  return resolvedReachStatus(reach, height, distance, from, target, outletId, geometry, reasons);
}

function outletTargetContext(outlet: SceneNode, geometry: WheelchairMotionGeometry, reasons: string[]) {
  const position: MotionPoint = { x: outlet.transform.m[3], z: -outlet.transform.m[7] };
  const heightAboveFloor = outletHeightAboveFloor(position, outlet.transform.m[11], geometry, reasons);
  const rawNormal = outlet.attachment?.normal;
  if (!rawNormal) reasons.push("Outlet support normal is missing; orientation unknown.");
  const normal = rawNormal ? normalize({ x: rawNormal.x, z: -rawNormal.y }, { x: 0, z: 1 }) : { x: 0, z: 1 };
  const unresolvedSocket = Boolean(outlet.attachment?.uncertainty_reasons?.some((reason) => reason.toLowerCase().includes("unresolved socket")));
  if (unresolvedSocket) reasons.push("Socket target is unresolved; cannot confirm operable reach.");
  return { position, heightAboveFloor, normal, hasNormal: Boolean(rawNormal), unresolvedSocket };
}

function outletReachDistance(
  current: MotionPoint | null | undefined,
  approach: ApproachSelection,
  target: MotionPoint,
): number | null {
  const origin = approach.status === "clear" && approach.candidate ? approach.candidate : current;
  return origin ? length(subtract(origin, target)) : null;
}

export function assessOutletAccessibility(
  currentPos: MotionPoint | null | undefined,
  outletNode: SceneNode,
  geometry: WheelchairMotionGeometry,
  profile: WheelchairProfile,
): OutletAccessibilityAssessment {
  const unresolvedReasons: string[] = [];
  if (!currentPos) unresolvedReasons.push("Current wheelchair position is unknown; route cannot be evaluated.");

  const target = outletTargetContext(outletNode, geometry, unresolvedReasons);
  const candidates = outletApproachCandidates(target.position, target.normal, currentPos, geometry, profile.collisionRadius);
  const approach = selectOutletApproach(currentPos, candidates, target.position, outletNode.id, geometry, profile.collisionRadius, unresolvedReasons);
  const reachDistance = outletReachDistance(currentPos, approach, target.position);
  const reachFromPoint = approach.candidate ?? currentPos;
  const reachStatus = outletReachStatus(
    profile, target.heightAboveFloor, reachDistance, reachFromPoint, target.position, outletNode.id,
    geometry, target.hasNormal, target.unresolvedSocket, unresolvedReasons,
  );

  return {
    approachStatus: approach.status,
    approachCandidate: approach.candidate,
    approachDistance: approach.distance,
    approachHeadingDeg: approach.heading,
    reachStatus,
    targetHeightAboveFloor: target.heightAboveFloor,
    reachDistance,
    unresolvedReasons,
    disclaimers: [...OUTLET_DISCLAIMERS],
  };
}
