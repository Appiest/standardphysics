/** How far, in screen pixels, a press may travel before its release ends a camera drag rather than making a tap. */
export const TAP_SLOP_PX = 5;

/**
 * The click the browser sends when a camera drag ends is not a tap on whatever sits under the pointer. Taking it
 * as one selected that piece and flew the camera to it, so every orbit that ended over furniture threw the view
 * somewhere new. `delta` is how far the pointer travelled since it was pressed, as React Three Fiber measures it.
 */
export function endsADrag(event: { delta: number }) {
  return event.delta > TAP_SLOP_PX;
}
