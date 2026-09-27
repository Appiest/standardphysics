"use client";

import { useCallback, useMemo, useState } from "react";
import type { ArrangeHandlers } from "@/components/workspace/ShopModel";
import type { RouteHandles } from "@/components/workspace/StopMarkers";
import type { Arrangement } from "@/components/workspace/useArrangement";
import { overviewPose, poseFromLocus, topDownPose } from "@/lib/camera";
import { canBeCounter } from "@/lib/counter";
import { type Panel, wheelchairStartFrom } from "@/lib/owner-journey";
import { stopMarkers } from "@/lib/route";
import type { Finding, Scenario, SceneGraph, Vec3 } from "@/types/contracts";
import type { ModelSetup } from "./OwnerModel";
import type { PathEditor } from "./usePathEditor";

/** The piece the scan already calls a counter, so the owner often only has to say yes. */
export function guessCounter(scene: SceneGraph | null): string | null {
  const named = scene?.nodes.filter((node) => canBeCounter(node) && node.label.toLowerCase().includes("counter")) ?? [];
  const largest = named.sort((a, b) => b.dimensions.x * b.dimensions.y - a.dimensions.x * a.dimensions.y)[0];
  return largest?.id ?? null;
}

function useRouteHandles(path: PathEditor | null, setDragging: (on: boolean) => void): RouteHandles | null {
  return useMemo(() => (path === null ? null : {
    markers: path.markers, editable: true, legs: path.legs,
    onGrab: () => setDragging(true), onDrag: path.drag, onDrop: () => { setDragging(false); path.settle(); },
  }), [path, setDragging]);
}

function useArrangeHandlers(arrangement: Arrangement | null, setDragging: (on: boolean) => void): ArrangeHandlers | null {
  return useMemo(() => (arrangement === null ? null : {
    activeId: arrangement.activeId,
    blockedIds: arrangement.blockedIds,
    onGrab: (nodeId: string) => { arrangement.setActiveId(nodeId); setDragging(true); },
    onDrag: arrangement.drag,
    onDrop: () => { setDragging(false); void arrangement.drop(); },
  }), [arrangement, setDragging]);
}

type Mode = {
  panel: Panel; scene: SceneGraph; selected: Finding | null; counter: string | null; path: PathEditor | null;
  arrangement: Arrangement | null; wheelchair: boolean; scenario: Scenario | null;
  /** The confirmed path's walking route, shown while driving the walk-through. */
  walkedLegs: Vec3[][];
};

const STAY_PUT = () => {};

/** The confirmed path, shown and not draggable, so the walk-through has somewhere to go. */
function useConfirmedRoute(scenario: Scenario | null, legs: Vec3[][], shown: boolean): RouteHandles | null {
  return useMemo(() => (!shown || !scenario ? null : {
    markers: stopMarkers(scenario), editable: false, legs, onGrab: STAY_PUT, onDrag: STAY_PUT, onDrop: STAY_PUT,
  }), [scenario, legs, shown]);
}

/** While choosing the counter, every piece stays tappable and the chosen one is outlined. */
function counterPicking(mode: Mode): { highlight: string[] | null; picking: boolean } {
  if (mode.panel !== "counter") return { highlight: null, picking: false };
  return { highlight: mode.counter ? [mode.counter] : null, picking: true };
}

/** Where the camera sits and what the model lets the owner touch, for whichever step is on screen. */
export function useOwnerModel(mode: Mode, onPickNode: (nodeId: string) => void, onClear: () => void): ModelSetup {
  const [dragging, setDragging] = useState(false);
  const editing = useRouteHandles(mode.path, setDragging);
  const confirmed = useConfirmedRoute(mode.scenario, mode.walkedLegs, mode.wheelchair);
  const route = editing ?? confirmed;
  const arrange = useArrangeHandlers(mode.arrangement, setDragging);
  const fromAbove = mode.panel === "counter" || mode.panel === "path";
  const overview = useMemo(() => (fromAbove ? topDownPose(mode.scene) : overviewPose(mode.scene)), [fromAbove, mode.scene]);
  const camera = mode.selected?.locus?.camera;
  const pose = useMemo(() => (camera ? poseFromLocus(camera) : overview), [camera, overview]);
  const pick = useCallback((nodeId: string) => onPickNode(nodeId), [onPickNode]);
  const wheelchairStart = useMemo(() => wheelchairStartFrom(mode.scenario), [mode.scenario]);
  return {
    shown: mode.arrangement?.shown ?? mode.scene,
    pose,
    selected: mode.selected,
    ...counterPicking(mode),
    wheelchair: mode.wheelchair,
    wheelchairStart,
    route,
    arrange,
    dragging,
    onSelectNode: pick,
    onClear,
  };
}
