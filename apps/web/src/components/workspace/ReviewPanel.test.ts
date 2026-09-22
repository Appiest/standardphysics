import { describe, expect, it } from "vitest";
import { isOutletNode } from "@/lib/review-targets";
import type { SceneNode } from "@/types/contracts";

const baseNode = (overrides: Partial<SceneNode> = {}): SceneNode => ({
  id: "node-1",
  kind: "wall",
  label: "Wall",
  raw_category: "wall",
  dimensions: { x: 4, y: 0.2, z: 3 },
  transform: { m: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1] },
  quality: "measured",
  movable: false,
  labeled_by: "roomplan",
  parent_id: null,
  ...overrides,
});

describe("ReviewPanel semantic filtering", () => {
  it("LIST-01: rejects wall/table nodes with attachment omitted, undefined or null", () => {
    const wallOmitted = baseNode({ kind: "wall" });
    const wallNull = { ...baseNode({ kind: "wall" }), attachment: null };
    const tableOmitted = baseNode({ kind: "object", raw_category: "table", label: "Table" });
    const tableNull = { ...baseNode({ kind: "object", raw_category: "table", label: "Table" }), attachment: null };

    const nodes = [wallOmitted, wallNull, tableOmitted, tableNull];
    const filtered = nodes.filter(isOutletNode);
    expect(filtered).toHaveLength(0);
  });

  it("LIST-02: includes only explicit outlet categories; excludes wall-mounted whiteboards", () => {
    const whiteboard = baseNode({
      id: "wb-1",
      kind: "whiteboard",
      label: "Whiteboard",
      raw_category: "whiteboard",
      attachment: {
        support_node_id: "wall-1",
        support_type: "lidar_surface",
        review_status: "detected",
        uncertainty_reasons: [],
        sockets: [],
        observations: [],
        localization_quality: "verified_support",
        identity_confidence: 0.9,
        local_anchor: null,
        normal: { x: 0, y: -1, z: 0 },
        observed_region: [],
      },
    });

    const realOutlet = baseNode({
      id: "outlet-1",
      kind: "outlet",
      label: "Photographed Outlet",
      raw_category: "outlet",
      attachment: {
        support_node_id: "wall-1",
        support_type: "lidar_surface",
        review_status: "detected",
        uncertainty_reasons: [],
        sockets: [],
        observations: [],
        localization_quality: "verified_support",
        identity_confidence: 0.9,
        local_anchor: null,
        normal: { x: 0, y: -1, z: 0 },
        observed_region: [],
      },
    });

    const candidateOutlet = baseNode({
      id: "cand-1",
      kind: "candidate_outlet",
      label: "Candidate Outlet",
      raw_category: "outlet",
      attachment: {
        support_node_id: "wall-1",
        support_type: "unanchored",
        review_status: "candidate",
        uncertainty_reasons: ["unanchored"],
        sockets: [],
        observations: [],
        localization_quality: "unanchored",
        identity_confidence: 0.5,
        local_anchor: null,
        normal: { x: 0, y: -1, z: 0 },
        observed_region: [],
      },
    });

    const nodes = [whiteboard, realOutlet, candidateOutlet];
    const filtered = nodes.filter(isOutletNode);
    expect(filtered).toHaveLength(2);
    expect(filtered.map((n) => n.id)).toEqual(["outlet-1", "cand-1"]);
    expect(filtered.find((n) => n.kind === "whiteboard")).toBeUndefined();
  });
});
