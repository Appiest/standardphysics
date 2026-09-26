import { composeRoomSheet } from "../../oneline/roomInk";
import type { WalkSheet } from "../../oneline/walkSheet";

export const FAST_INK_ZOOM = 1.5;

/** The fast reel's sheet is the room alone: the four walks, walls and furniture, with no schematic nodes. */
export function composeFastSheet(sheet: WalkSheet) {
  return composeRoomSheet(sheet, FAST_INK_ZOOM);
}
