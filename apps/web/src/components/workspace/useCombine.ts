"use client";

import { useRouter } from "next/navigation";
import { useCallback, useMemo, useState } from "react";
import { ApiRefusal, saveCombine } from "@/lib/layout-client";
import { applyRoomPlacements, centroid, type RoomGroup, type RoomPlacement } from "@/lib/room-groups";
import type { SceneGraph } from "@/types/contracts";

export type Combine = ReturnType<typeof useCombine>;

export function useCombine(scanId: string, scene: SceneGraph, rooms: RoomGroup[]) {
  const router = useRouter();
  const [placements, setPlacements] = useState<Record<string, RoomPlacement>>({});
  const [activeRoom, setActiveRoom] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [savedRevision, setSavedRevision] = useState(scene.revision);

  if (scene.revision !== savedRevision) {
    setSavedRevision(scene.revision);
    setPlacements({});
    setActiveRoom(null);
  }

  const roomByNode = useMemo(() => {
    const map = new Map<string, string>();
    for (const room of rooms) for (const nodeId of room.node_ids) map.set(nodeId, room.name);
    return map;
  }, [rooms]);

  const centroids = useMemo(() => {
    const map = new Map<string, [number, number]>();
    for (const room of rooms) {
      const nodes = scene.nodes.filter((node) => roomByNode.get(node.id) === room.name);
      map.set(room.name, centroid(nodes));
    }
    return map;
  }, [rooms, scene, roomByNode]);

  const base = useCallback(
    (room: string): RoomPlacement => {
      const [cx, cy] = centroids.get(room) ?? [0, 0];
      return { yawDegrees: 0, tx: 0, ty: 0, cx, cy };
    },
    [centroids],
  );

  const shown = useMemo(() => applyRoomPlacements(scene, rooms, placements), [scene, rooms, placements]);

  const update = useCallback(
    (room: string, patch: Partial<RoomPlacement>) =>
      setPlacements((current) => ({ ...current, [room]: { ...(current[room] ?? base(room)), ...patch } })),
    [base],
  );

  const drag = useCallback(
    (nodeId: string, dx: number, dy: number) => {
      const room = roomByNode.get(nodeId);
      if (!room) return;
      setPlacements((current) => {
        const placement = current[room] ?? base(room);
        return { ...current, [room]: { ...placement, tx: placement.tx + dx, ty: placement.ty + dy } };
      });
    },
    [roomByNode, base],
  );

  const nudge = useCallback(
    (dx: number, dy: number, degrees: number) => {
      if (!activeRoom) return;
      setPlacements((current) => {
        const placement = current[activeRoom] ?? base(activeRoom);
        return {
          ...current,
          [activeRoom]: {
            ...placement,
            tx: placement.tx + dx,
            ty: placement.ty + dy,
            yawDegrees: (((placement.yawDegrees + degrees) % 360) + 360) % 360,
          },
        };
      });
    },
    [activeRoom, base],
  );

  const onGrab = useCallback((nodeId: string) => setActiveRoom(roomByNode.get(nodeId) ?? null), [roomByNode]);

  const setYaw = useCallback(
    (degrees: number) => {
      if (!activeRoom) return;
      const clamped = ((degrees % 360) + 360) % 360;
      update(activeRoom, { yawDegrees: clamped });
    },
    [activeRoom, update],
  );

  const reset = useCallback(() => {
    setPlacements({});
    setActiveRoom(null);
    setProblem(null);
  }, []);

  const save = useCallback(async () => {
    setSaving(true);
    try {
      const payload = rooms.map((room) => {
        const placement = placements[room.name] ?? base(room.name);
        return {
          node_ids: room.node_ids,
          yaw_degrees: placement.yawDegrees,
          tx: placement.tx,
          ty: placement.ty,
          cx: placement.cx,
          cy: placement.cy,
        };
      });
      await saveCombine(scanId, scene.revision, payload);
      setActiveRoom(null);
      setProblem(null);
      router.refresh();
      setTimeout(() => router.refresh(), 2500);
      return true;
    } catch (error) {
      const stale = error instanceof ApiRefusal && error.status === 409;
      if (stale) {
        reset();
        router.refresh();
        setProblem("Someone saved a newer layout, so we loaded it. Align the rooms again on this one.");
      } else {
        setProblem("That arrangement couldn't be saved. Try again.");
      }
      return false;
    } finally {
      setSaving(false);
    }
  }, [rooms, placements, base, scanId, scene.revision, reset, router]);

  const hasMoves = Object.keys(placements).length > 0;

  return {
    shown, placements, activeRoom, setActiveRoom, hasMoves, saving, problem,
    rooms, centroids, onGrab, onDrag: drag, onDrop: () => {}, nudge, setYaw, reset, save,
  };
}
