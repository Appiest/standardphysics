"use client";

import dynamic from "next/dynamic";
import type { ArrangeHandlers } from "@/components/workspace/ShopModel";
import type { RouteHandles } from "@/components/workspace/StopMarkers";
import type { WheelchairStart } from "@/components/workspace/Viewer";
import type { ViewerPose } from "@/lib/camera";
import type { Focus } from "@/lib/findings";
import { DEFAULT_WHEELCHAIR_PROFILE } from "@/lib/wheelchair-motion";
import type { SceneGraph } from "@/types/contracts";

const Viewer = dynamic(() => import("@/components/workspace/Viewer"), {
  ssr: false,
  loading: () => <div className="size-full animate-pulse bg-rule/40 motion-reduce:animate-none" />,
});

const NO_NODES: string[] = [];
const IGNORE_STATE = () => {};
const NO_COVERAGE: [] = [];

/** What the model shows for the step on screen: where the camera sits, what's picked, and what can be dragged. */
export type ModelSetup = {
  shown: SceneGraph;
  pose: ViewerPose;
  selected: Focus | null;
  highlight: string[] | null;
  picking: boolean;
  /** Seated, first-person driving for the wheelchair walk-through. */
  wheelchair: boolean;
  wheelchairStart: WheelchairStart | null;
  route: RouteHandles | null;
  arrange: ArrangeHandlers | null;
  dragging: boolean;
  onSelectNode: (nodeId: string) => void;
  onClear: () => void;
};

export function OwnerModel({ scene, glbUrl, setup, lightweight }: { scene: SceneGraph; glbUrl: string | null; setup: ModelSetup; lightweight: boolean }) {
  return (
    <Viewer
      scene={setup.shown}
      exported={scene}
      highlightNodeIds={setup.highlight}
      picking={setup.picking}
      arrange={setup.arrange}
      route={setup.route}
      dragging={setup.dragging}
      cutWalls={!setup.wheelchair}
      glbUrl={glbUrl}
      lidarUrl={null}
      pose={setup.pose}
      selected={setup.selected}
      onSelectNode={setup.onSelectNode}
      onClearSelection={setup.onClear}
      materialMode={glbUrl ? "reconstructed" : "plain"}
      scanGlbUrl={null}
      staleNodeIds={NO_NODES}
      coverage={NO_COVERAGE}
      lightweight={lightweight}
      wheelchairMode={setup.wheelchair}
      wheelchairProfile={DEFAULT_WHEELCHAIR_PROFILE}
      onWheelchairStateChange={IGNORE_STATE}
      wheelchairStart={setup.wheelchairStart}
    />
  );
}
