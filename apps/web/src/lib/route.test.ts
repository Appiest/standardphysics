import { describe, expect, it } from "vitest";
import type { Scenario } from "@/types/contracts";
import { moveMarker, stopMarkers } from "./route";

const at = (name: string, x: number, y: number) => ({ name, position: { x, y, z: 0 }, anchor_node_id: null });
const route: Scenario = {
  name: "Order a drink",
  stops: [at("Entrance", 0, -3), at("Counter", 0, 3), at("Seat", -2, 1), at("Exit", 0, -3)],
};

describe("stopMarkers", () => {
  it("draws an entrance that is also the exit as one marker", () => {
    const markers = stopMarkers(route);
    expect(markers.map((marker) => marker.label)).toEqual(["Entrance and exit", "Counter", "Seat"]);
    expect(markers[0].stopIndexes).toEqual([0, 3]);
  });

  it("lifts the label of a stop that crowds an earlier one", () => {
    const crowded: Scenario = { ...route, stops: [at("Entrance", 0, -3), at("Counter", 0.3, 3.1), at("Pickup", 0.8, 3.1), at("Exit", 0, -3)] };
    expect(stopMarkers(crowded).map((marker) => [marker.label, marker.labelTier])).toEqual([
      ["Entrance and exit", 0], ["Counter", 0], ["Pickup", 1],
    ]);
  });
});

describe("labels across a whole shop", () => {
  it("stacks the labels of stops two metres apart, which overlap on screen when the whole shop is in view", () => {
    const near: Scenario = { ...route, stops: [at("Entrance", 0, 0), at("Counter", 2, 0.4), at("Exit", 0, 0)] };
    expect(stopMarkers(near).map((marker) => marker.labelTier)).toEqual([0, 1]);
  });
});

describe("moveMarker", () => {
  it("moves every stop that shares the marker, and nothing else", () => {
    const moved = moveMarker(route, stopMarkers(route)[0], 0.5, 0.25);
    expect(moved.stops.map((stop) => [stop.position.x, stop.position.y])).toEqual([[0.5, -2.75], [0, 3], [-2, 1], [0.5, -2.75]]);
  });
});
