"use client";

import { Suspense, useEffect } from "react";
import { IDENTITY_PLACEMENT, roomMeshPose, type RoomGroup, type RoomPlacement } from "@/lib/room-groups";
import { SCAN_CUT_HEIGHT } from "./palette";
import { PaintedScan } from "./PaintedScan";

/**
 * Every walk of a floor as the photographs found it, standing where its boxes stand.
 *
 * Four walks can only be fitted together by somebody who recognises them, and
 * the boxes give nothing to recognise: white, featureless, and one room much
 * like another. Each walk was uploaded as its own scan and photographed onto
 * its mesh, so what gets dragged here is the shelving and the signage rather
 * than a cluster of blocks.
 *
 * A walk still on its way, or one never photographed, simply does not draw. Its
 * boxes are underneath and keep it draggable, so a missing mesh costs detail
 * and never the ability to place the room.
 *
 * What draws is the photographed model, not the scanned surface itself. Four
 * captures of one library floor hold some sixteen million measured faces
 * between them and no browser gets through that; the models wearing the same
 * photographs are a tenth of the size and a thousandth of the geometry.
 *
 * A walk whose model will not load says so in the console rather than leaving
 * an empty floor and no reason for it, which is how this went unnoticed once
 * already.
 */
export function CombinedRooms({
  rooms,
  placements,
}: {
  rooms: RoomGroup[];
  placements: Record<string, RoomPlacement>;
}) {
  return (
    <group>
      {rooms.map((room) => {
        if (!room.scan_glb_url) {
          return <MissingMesh key={room.name} name={room.name} />;
        }
        const { position, yaw } = roomMeshPose(placements[room.name] ?? IDENTITY_PLACEMENT, room.capture_pose ?? undefined);
        return (
          <group key={room.name} position={position} rotation={[0, yaw, 0]}>
            <Suspense fallback={null}>
              <PaintedScan url={room.scan_glb_url} cutAbove={SCAN_CUT_HEIGHT} />
            </Suspense>
          </group>
        );
      })}
    </group>
  );
}

/** Says which walk has no model, so an empty floor is never a mystery. */
function MissingMesh({ name }: { name: string }) {
  useEffect(() => {
    console.warn(`[combine] no photographed model for the walk "${name}"; its boxes are all there is to drag`);
  }, [name]);
  return null;
}
