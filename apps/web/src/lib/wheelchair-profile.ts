export type ReachProfile = {
  maxReachDistance?: number;
  minReachHeight?: number;
  maxReachHeight?: number;
  handReference?: string | null;
  distanceInterval?: [number, number];
  heightInterval?: [number, number];
};

/** Adjustable navigation estimates, not a model-specific wheelchair measurement. */
export type WheelchairProfile = {
  eyeHeight: number;
  speed: number;
  collisionRadius: number;
  reach?: ReachProfile | null;
};

export const WHEELCHAIR_PROFILE_LIMITS = {
  eyeHeight: { min: 0.75, max: 1.45 },
  speed: { min: 0.6, max: 4 },
  collisionRadius: { min: 0.3, max: 0.7 },
  minReachHeight: { min: 0.1, max: 0.8 },
  maxReachHeight: { min: 0.8, max: 1.8 },
  maxReachDistance: { min: 0.2, max: 1.2 },
} as const;

export const DEFAULT_WHEELCHAIR_PROFILE: WheelchairProfile = {
  eyeHeight: 1.15,
  speed: 2.4,
  collisionRadius: 0.45,
  reach: null,
};

function boundedEstimate(value: unknown, limits: { min: number; max: number }, fallback: number): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return fallback;
  return Math.min(limits.max, Math.max(limits.min, value));
}

/** Keeps user-entered navigation estimates finite and within the conservative UI range. */
export function wheelchairProfile(profile: Partial<WheelchairProfile> | null | undefined): WheelchairProfile {
  return {
    eyeHeight: boundedEstimate(profile?.eyeHeight, WHEELCHAIR_PROFILE_LIMITS.eyeHeight, DEFAULT_WHEELCHAIR_PROFILE.eyeHeight),
    speed: boundedEstimate(profile?.speed, WHEELCHAIR_PROFILE_LIMITS.speed, DEFAULT_WHEELCHAIR_PROFILE.speed),
    collisionRadius: boundedEstimate(profile?.collisionRadius, WHEELCHAIR_PROFILE_LIMITS.collisionRadius, DEFAULT_WHEELCHAIR_PROFILE.collisionRadius),
    ...(profile?.reach !== undefined ? { reach: profile.reach ? { ...profile.reach } : null } : {}),
  };
}
