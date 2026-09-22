/** Pointer math for marking a photo: displayed pixels to stored-frame sensor pixels. */

export type PointerBox = { left: number; top: number; right: number; bottom: number };

/**
 * A rectangle the person drew on the photo, in stored-frame pixels, using the
 * image's natural size as the frame's true size. Displayed coordinates are
 * scaled by the ratio between the rendered box and the natural box.
 */
export function sensorBoxFromPointer(
  start: { x: number; y: number },
  end: { x: number; y: number },
  rect: { left: number; top: number; width: number; height: number },
  natural: { width: number; height: number }
): PointerBox {
  const scaleX = natural.width / rect.width;
  const scaleY = natural.height / rect.height;
  const left = Math.min(start.x, end.x) - rect.left;
  const top = Math.min(start.y, end.y) - rect.top;
  const right = Math.max(start.x, end.x) - rect.left;
  const bottom = Math.max(start.y, end.y) - rect.top;
  return {
    left: Math.max(0, Math.round(left * scaleX)),
    top: Math.max(0, Math.round(top * scaleY)),
    right: Math.min(natural.width, Math.round(right * scaleX)),
    bottom: Math.min(natural.height, Math.round(bottom * scaleY)),
  };
}

/** A drawn box is only a usable crop request when it is a positive rectangle. */
export function isUsablePointerBox(box: PointerBox): boolean {
  return Number.isFinite(box.left) && Number.isFinite(box.top) &&
    box.right > box.left && box.bottom > box.top;
}

/** The stored-frame scale factor a pointer box was computed with, for display labels. */
export function pointerBoxLabel(box: PointerBox): string {
  return `Frame pixels ${box.left}–${box.right} across, ${box.top}–${box.bottom} down`;
}
