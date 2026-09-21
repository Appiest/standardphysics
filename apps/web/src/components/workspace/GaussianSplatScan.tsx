"use client";

import { useEffect, useMemo, useRef } from "react";
import { useThree } from "@react-three/fiber";
import { Group, Matrix4 } from "three";
import type { SparkRenderer, SplatMesh } from "@sparkjsdev/spark";
import { toViewerMatrix } from "@/lib/scene-matrix";
import type { CapturedSplatAsset } from "@/lib/captured-splats";
import type { Mat4 } from "@/types/contracts";

type GaussianSplatScanProps = {
  assets: CapturedSplatAsset[];
  onReady?: () => void;
  onError?: (error: Error) => void;
};

const ZUP_TO_YUP = new Matrix4().set(
  1, 0, 0, 0,
  0, 0, 1, 0,
  0, -1, 0, 0,
  0, 0, 0, 1,
);

export function toSplatViewerMatrix(transform: number[]): Matrix4 {
  if (transform.length !== 16 || transform.some((value) => !Number.isFinite(value))) {
    throw new Error("Gaussian splat alignment must be a finite row-major 4x4 matrix");
  }

  // toViewerMatrix converts scene transforms for already-Y-up local content as
  // B * alignment * B^-1. Brush PLY vertices are themselves Z-up, so restore
  // their local basis to obtain B * alignment.
  return toViewerMatrix({ m: transform } as Mat4).multiply(ZUP_TO_YUP);
}

function asError(error: unknown): Error {
  return error instanceof Error ? error : new Error(String(error));
}

/** Visual evidence only. SceneGraph geometry continues to own picking and collision. */
export function GaussianSplatScan({ assets, onReady, onError }: GaussianSplatScanProps) {
  const { gl, invalidate, scene } = useThree();
  const readyRef = useRef(onReady);
  const errorRef = useRef(onError);
  const assetsRef = useRef(assets);
  const assetKey = useMemo(
    () => JSON.stringify(assets.map(({ url, transform }) => ({ url, transform }))),
    [assets],
  );

  useEffect(() => {
    readyRef.current = onReady;
    errorRef.current = onError;
    assetsRef.current = assets;
  }, [assets, onError, onReady]);

  useEffect(() => {
    const currentAssets = assetsRef.current;
    if (currentAssets.length === 0) return;

    let disposed = false;
    let cleaned = false;
    let group: Group | undefined;
    let spark: SparkRenderer | undefined;
    let splats: SplatMesh[] = [];
    let contextLost = false;

    function cleanup() {
      if (cleaned) return;
      cleaned = true;
      gl.domElement.removeEventListener("webglcontextlost", handleContextLost);
      for (const splat of splats) {
        splat.removeFromParent();
        splat.dispose();
      }
      splats = [];
      group?.removeFromParent();
      spark?.removeFromParent();
      spark?.dispose();
      invalidate();
    }

    const handleContextLost = (event: Event) => {
      event.preventDefault();
      contextLost = true;
      cleanup();
      if (!disposed) errorRef.current?.(new Error("The Gaussian splat WebGL context was lost"));
    };

    gl.domElement.addEventListener("webglcontextlost", handleContextLost);

    // eslint-disable-next-line complexity
    void (async () => {
      try {
        if (!gl.capabilities.isWebGL2) {
          throw new Error("Gaussian splats require WebGL2 on this device");
        }

        const { SparkRenderer, SplatMesh } = await import("@sparkjsdev/spark");
        if (disposed || contextLost) return;

        spark = new SparkRenderer({ renderer: gl, onDirty: invalidate });
        const splatGroup = new Group();
        group = splatGroup;
        splatGroup.name = "Gaussian splat scan";
        scene.add(spark, splatGroup);

        const preparedAssets = currentAssets.map(({ url, transform }) => ({
          url,
          matrix: toSplatViewerMatrix(transform),
        }));
        for (const { url, matrix } of preparedAssets) {
          const splat = new SplatMesh({
            url,
            // Build Spark's level-of-detail tree so its platform-specific
            // rendering budget applies across all four captures.
            lod: true,
            editable: false,
            raycastable: false,
            onLoad: () => invalidate(),
          });
          splats.push(splat);
          splat.matrix.copy(matrix);
          splat.matrixAutoUpdate = false;
          splat.matrixWorldNeedsUpdate = true;
          splatGroup.add(splat);
        }

        await Promise.all(splats.map((splat) => splat.initialized));
        if (disposed || contextLost) return;
        invalidate();
        readyRef.current?.();
      } catch (error) {
        cleanup();
        if (!disposed && !contextLost) errorRef.current?.(asError(error));
      }
    })();

    return () => {
      disposed = true;
      cleanup();
    };
  }, [assetKey, gl, invalidate, scene]);

  return null;
}
