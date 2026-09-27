import type { FunnelStep } from "@/types/contracts";

export type FunnelBar = FunnelStep & { share: number; lost: number };

/** Each step as a share of the shops that started, and how many the step before it lost. */
export function funnelBars(steps: FunnelStep[]): FunnelBar[] {
  const started = Math.max(steps[0]?.shops ?? 0, 1);
  return steps.map((step, index) => ({
    ...step,
    share: step.shops / started,
    lost: index === 0 ? 0 : Math.max(steps[index - 1].shops - step.shops, 0),
  }));
}

export function duration(minutes: number | null, unit: "minutes" | "hours"): string {
  if (minutes === null) return "Not yet";
  return `${minutes} ${unit === "minutes" ? (minutes === 1 ? "minute" : "minutes") : minutes === 1 ? "hour" : "hours"}`;
}
