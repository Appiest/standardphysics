/** Pointer math for marking a photo: displayed pixels to stored-frame sensor pixels. */

export type PointerBox = { left: number; top: number; right: number; bottom: number };

export type RectLike = { left: number; top: number; width: number; height: number };

/**
 * The rectangle the visible photo actually occupies inside an `<img>` element
 * sized with CSS `object-contain`. The element box is letterboxed: bars of
 * background surround the content when the frame's aspect ratio differs from
 * the element's. Pointing must be measured against this content rectangle,
 * never against the element box, or every box shifts when the photo is
 * portrait inside a landscape element (the phone case).
 */
export function containedContentRect(rect: RectLike, natural: { width: number; height: number }): RectLike {
  if (natural.width === 0 || natural.height === 0 || rect.width === 0 || rect.height === 0) return rect;
  const scale = Math.min(rect.width / natural.width, rect.height / natural.height);
  const width = natural.width * scale;
  const height = natural.height * scale;
  return {
    left: rect.left + (rect.width - width) / 2,
    top: rect.top + (rect.height - height) / 2,
    width,
    height,
  };
}

/**
 * A rectangle the person drew on the photo, in stored-frame pixels, using the
 * image's natural size as the frame's true size. Displayed coordinates are
 * first mapped through the object-contain content rectangle, so letterboxed
 * layouts report the same box as unpadded ones.
 */
export function sensorBoxFromPointer(
  start: { x: number; y: number },
  end: { x: number; y: number },
  rect: RectLike,
  natural: { width: number; height: number }
): PointerBox {
  const content = containedContentRect(rect, natural);
  const scaleX = natural.width / content.width;
  const scaleY = natural.height / content.height;
  const left = Math.min(start.x, end.x) - content.left;
  const top = Math.min(start.y, end.y) - content.top;
  const right = Math.max(start.x, end.x) - content.left;
  const bottom = Math.max(start.y, end.y) - content.top;
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
