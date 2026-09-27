import { getAssessment, getJourney, getScenario, getScene } from "@/lib/api";
import { scanStatus } from "@/lib/scan-status";
import type { Assessment, Scan, SceneGraph } from "@/types/contracts";

export interface ShopSheet {
  scan: Scan;
  scene: SceneGraph | null;
  assessment: Assessment | null;
  hasScenario: boolean;
  /** The owner view for owners, the builders' workspace for the team. */
  href: string;
  /** The shop's one next step for owners; the scan's state for the team. */
  status: string;
}

export async function loadShopSheet(scan: Scan, team: boolean): Promise<ShopSheet> {
  const [scene, assessment, scenario, journey] = await Promise.all([
    getScene(scan.id), getAssessment(scan.id), getScenario(scan.id), team ? null : getJourney(scan.id),
  ]);
  const hasScenario = scenario !== null;
  return {
    scan, scene, assessment, hasScenario,
    href: team ? `/scans/${scan.id}` : `/shops/${scan.id}`,
    status: journey?.next_step.title ?? scanStatus(scan, assessment, hasScenario),
  };
}

export function sheetNumber(index: number) {
  return `A-${101 + index}`;
}
