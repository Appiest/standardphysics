export type ViewerMaterialMode = "reconstructed" | "captured" | "plain" | "coverage" | "scan";

type ViewerSourceInput = {
  materialMode: ViewerMaterialMode;
  hasCleanGlb: boolean;
  hasPhotoBuild: boolean;
  staleNodeIds: string[];
};

/** Selects the source-specific rules that keep a current clean GLB independent of an older photo build. */
export function viewerSourcePlan({ materialMode, hasCleanGlb, hasPhotoBuild, staleNodeIds }: ViewerSourceInput) {
  const usePhotoBuild = hasPhotoBuild && (materialMode === "captured" || materialMode === "coverage");
  return {
    usePhotoBuild,
    staleNodeIds: usePhotoBuild ? staleNodeIds : [],
    materialMode: materialMode === "scan" || usePhotoBuild || hasCleanGlb ? materialMode : "plain" as const,
  };
}
