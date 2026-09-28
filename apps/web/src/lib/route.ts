import type { Scenario } from "@/types/contracts";

export type StopMarker = { key: string; label: string; stopIndexes: number[]; x: number; y: number; labelTier: number };

const SAME_SPOT_METERS = 0.05;
/** Labels are about two metres wide on screen when the whole shop is in view, so stops closer than this collide. */
const CROWDED_METERS = 2.5;

/** One marker per spot, so an entrance that is also the exit is dragged as one. */
export function stopMarkers(scenario: Scenario): StopMarker[] {
  const markers: StopMarker[] = [];
  scenario.stops.forEach((stop, index) => {
    const { x, y } = stop.position;
    const shared = markers.find((marker) => Math.hypot(marker.x - x, marker.y - y) < SAME_SPOT_METERS);
    if (shared) {
      shared.stopIndexes.push(index);
      shared.label = `${shared.label} and ${stop.name.toLowerCase()}`;
      return;
    }
    markers.push({ key: `${index}`, label: stop.name, stopIndexes: [index], x, y, labelTier: 0 });
  });
  return stackCrowdedLabels(markers);
}

/** A marker close to earlier ones lifts its label above theirs, so Counter and Pickup stay readable. */
function stackCrowdedLabels(markers: StopMarker[]): StopMarker[] {
  return markers.map((marker, index) => ({
    ...marker,
    labelTier: markers.slice(0, index).filter((earlier) => Math.hypot(earlier.x - marker.x, earlier.y - marker.y) < CROWDED_METERS).length,
  }));
}

export function moveMarker(scenario: Scenario, marker: StopMarker, dx: number, dy: number): Scenario {
  const moving = new Set(marker.stopIndexes);
  return {
    ...scenario,
    stops: scenario.stops.map((stop, index) =>
      moving.has(index)
        ? { ...stop, position: { ...stop.position, x: stop.position.x + dx, y: stop.position.y + dy } }
        : stop,
    ) as Scenario["stops"],
  };
}
