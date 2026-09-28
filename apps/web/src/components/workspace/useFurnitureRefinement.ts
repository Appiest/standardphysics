"use client";

import { useCallback, useEffect, useState } from "react";
import type { TextureStatus } from "@/types/contracts";
import type { FurnitureRefinement } from "./ViewerDock";

type Snapshot = { buildId: string; status: FurnitureRefinement };

async function fetchFurniture(scanId: string, revision: number): Promise<FurnitureRefinement | null> {
  const response = await fetch(`/api/scans/${scanId}/furniture?revision=${revision}`, { cache: "no-store" });
  return response.ok ? await response.json() as FurnitureRefinement : null;
}

function needsAnotherPoll(state: FurnitureRefinement["state"]): boolean {
  return state === "queued" || state === "running" || state === "not_started";
}

export function useFurnitureRefinement(
  scanId: string, revision: number, textures: TextureStatus | null, refreshTextures: () => Promise<void>,
) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [retryCount, setRetryCount] = useState(0);
  const buildId = textures?.build?.build_id ?? null;
  const ready = textures?.exact && textures.state === "complete";

  useEffect(() => {
    if (!buildId || !ready) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const poll = async () => {
      try {
        const result = await fetchFurniture(scanId, revision);
        if (!active || !result || result.build_id !== buildId) return;
        setSnapshot({ buildId, status: result });
        if (result.state === "done") { await refreshTextures(); return; }
        if (needsAnotherPoll(result.state)) timer = setTimeout(() => { void poll(); }, 5000);
      } catch {
        if (active) timer = setTimeout(() => { void poll(); }, 10000);
      }
    };
    void poll();
    return () => { active = false; if (timer) clearTimeout(timer); };
  }, [buildId, ready, refreshTextures, retryCount, revision, scanId]);

  const retryFurniture = useCallback(async () => {
    try {
      const response = await fetch(`/api/scans/${scanId}/furniture?revision=${revision}`, { method: "POST" });
      if (!response.ok) throw new Error("Furniture retry failed");
      setRetryCount((count) => count + 1);
    } catch {
      if (buildId) setSnapshot({ buildId, status: { build_id: buildId, state: "failed", error: "Couldn’t retry furniture. Try again." } });
    }
  }, [buildId, revision, scanId]);

  return { furniture: snapshot?.buildId === buildId ? snapshot.status : null, retryFurniture };
}
