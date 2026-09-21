import { getAssessment, getScenario, getScene } from "@/lib/api";
import type { Assessment, Scan, SceneGraph } from "@/types/contracts";

export interface ShopSheet {
  scan: Scan;
  scene: SceneGraph | null;
  assessment: Assessment | null;
  hasScenario: boolean;
}

export async function loadShopSheet(scan: Scan): Promise<ShopSheet> {
  const [scene, assessment, scenario] = await Promise.all([getScene(scan.id), getAssessment(scan.id), getScenario(scan.id)]);
  return { scan, scene, assessment, hasScenario: scenario !== null };
}

export function sheetNumber(index: number) {
  return `A-${101 + index}`;
}
