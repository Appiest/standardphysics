"use client";

import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";
import { confirmRoute } from "@/lib/layout-client";
import type { Scenario } from "@/types/contracts";
import { useStaffEditing } from "./usePathEditor";

/**
 * The staff-only floor on the confirmed path, which the owner can still move
 * or resize from the results. Each drop saves the path again, so the checks
 * rerun against the new area; a save that fails puts the area back.
 */
export function useStaffAdjuster(scanId: string, confirmed: Scenario | null) {
  const router = useRouter();
  const [scenario, setScenario] = useState(confirmed);
  const [confirmedBefore, setConfirmedBefore] = useState(confirmed);
  if (confirmed !== confirmedBefore) {
    setConfirmedBefore(confirmed);
    setScenario(confirmed);
  }
  const { moveStaff, resizeStaff } = useStaffEditing(setScenario);

  const save = useCallback(() => {
    if (!scenario || scenario === confirmed) return;
    confirmRoute(scanId, scenario)
      .then(() => router.refresh())
      .catch(() => setScenario(confirmed));
  }, [scanId, scenario, confirmed, router]);

  return { areas: scenario?.staff_only ?? [], moveStaff, resizeStaff, save };
}

export type StaffAdjuster = ReturnType<typeof useStaffAdjuster>;
