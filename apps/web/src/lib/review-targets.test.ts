import { describe, expect, it } from "vitest";
import { classEmptyDetail, firstRenderableCrop, nodePosition, nodeTargetClass, reviewEntriesFor } from "@/lib/review-targets";
import type { ObservationCrop, SceneGraph, SceneNode } from "@/types/contracts";

const node = (overrides: Partial<SceneNode> = {}): SceneNode => ({
  id: "node-1",
  kind: "wall",
  label: "Wall",
  raw_category: "wall",
  dimensions: { x: 1, y: 1, z: 1 },
  transform: { m: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0.5, 1.5, 1, 1] },
  quality: "measured",
  movable: false,
  labeled_by: "roomplan",
  parent_id: null,
  ...overrides,
});

const attachmentNode = (kind: string): SceneNode =>
  node({
    id: kind,
    kind,
    label: kind,
    raw_category: kind,
    attachment: {
      support_node_id: "wall-1",
      support_type: "lidar_surface",
      review_status: "detected",
      uncertainty_reasons: [],
      sockets: [],
      observations: [{ frame_id: "f-1", sensor_box: [0, 0, 10, 10], confidence: 0.9, image_url: "crop-1", provenance: "automatic", marked_by: null, marked_at: null, note: null }],
      localization_quality: "verified_support",
      identity_confidence: 0.9,
      local_anchor: null,
      normal: null,
      observed_region: [],
    },
  });

const graph = (nodes: SceneNode[], unlocalized: SceneGraph["unlocalized_observations"] = []): SceneGraph => ({
  scan_id: "scan-1",
  revision: 1,
  base_hash: "h",
  nodes,
  unlocalized_observations: unlocalized,
});

describe("review target matching", () => {
  it("reads the approximate position from the stored transform only when finite", () => {
    expect(nodePosition(node())).toEqual({ x: 0.5, y: 1.5, z: 1 });
    expect(nodePosition({ ...node(), transform: { m: [1, 2, 3] as unknown as SceneNode["transform"]["m"] } })).toBeNull();
  });

  it("a television with photo evidence is a television, not an outlet", () => {
    expect(nodeTargetClass(attachmentNode("television"))).toBe("television");
    expect(nodeTargetClass(attachmentNode("candidate_television"))).toBe("television");
    expect(nodeTargetClass(attachmentNode("outlet"))).toBe("outlet");
    expect(nodeTargetClass(attachmentNode("candidate_outlet"))).toBe("outlet");
  });

  it("the owner-marked service counter is its own class", () => {
    const counter = node({ kind: "object", label: "service counter", labeled_by: "owner", raw_category: "table" });
    expect(nodeTargetClass(counter)).toBe("service_counter");
  });

  it("a restroom entrance matches by name even before a dedicated pipeline type exists", () => {
    expect(nodeTargetClass(node({ kind: "opening", label: "Restroom door" }))).toBe("restroom_entrance");
  });

  it("an ordinary wall is no target class", () => {
    expect(nodeTargetClass(node())).toBeNull();
  });

  it("manual unlocalized observations stay their declared class and never gain a position", () => {
    const scene = graph([node()], [
      {
        id: "u-1",
        target_class: "television",
        frame_id: "f-9",
        sensor_box: [5, 5, 55, 55],
        provenance: "manual",
        review_status: "candidate",
        marked_by: "owner@example.com",
        marked_at: "2026-09-21T00:00:00Z",
        note: null,
      },
    ]);
    const televisions = reviewEntriesFor(scene, "television");
    expect(televisions).toHaveLength(1);
    expect(televisions[0].source).toBe("unlocalized");
    expect(televisions[0]).not.toHaveProperty("position");
  });

  it("an empty class says unknown, never that the room has none", () => {
    const detail = classEmptyDetail("restroom_entrance");
    expect(detail).toContain("does not know");
    expect(detail).not.toContain("none");
    expect(detail).toContain("Mark one in a photo");
  });

  it("entries collect nodes and unlocalized marks for the requested class only", () => {
    const scene = graph([attachmentNode("outlet"), attachmentNode("television")]);
    expect(reviewEntriesFor(scene, "outlet")).toHaveLength(1);
    expect(reviewEntriesFor(scene, "television")).toHaveLength(1);
    expect(reviewEntriesFor(scene, "all")).toHaveLength(2);
    expect(reviewEntriesFor(scene, "service_counter")).toHaveLength(0);
  });

  it("a scan with zero detector observations reads unknown for every class, like the real whiteboard scan", () => {
    const scene = graph([
      node({ id: "wall-1", kind: "wall", raw_category: "wall" }),
      node({ id: "door-1", kind: "door", raw_category: "door" }),
      node({ id: "floor-1", kind: "floor", raw_category: "floor" }),
      node({ id: "obj-1", kind: "object", raw_category: "wardrobe" }),
    ]);
    for (const targetClass of ["outlet", "television", "service_counter", "restroom_entrance"] as const) {
      expect(reviewEntriesFor(scene, targetClass)).toHaveLength(0);
    }
    expect(reviewEntriesFor(scene, "all")).toHaveLength(0);
    expect(classEmptyDetail("outlet")).toContain("does not know");
  });

  it("a persisted manual mark readback shows its server-cut crop like an automatic one", () => {
    const crop: ObservationCrop = {
      frame_id: "frame-0000",
      sensor_box: [40, 60, 200, 180],
      confidence: 1,
      image_url: "crop-abc",
      provenance: "manual",
      marked_by: "owner@example.com",
      marked_at: "2026-09-22T00:00:00Z",
      note: "outlet next to the desk",
    };
    const scene = graph([node({
      id: "n-9",
      kind: "outlet",
      label: "Photographed Outlet",
      raw_category: "outlet",
      attachment: {
        support_node_id: "wall-1",
        support_type: "unanchored",
        review_status: "confirmed_by_user",
        uncertainty_reasons: ["manual photo mark"],
        sockets: [],
        observations: [crop],
        localization_quality: "unanchored",
        identity_confidence: 1,
        local_anchor: null,
        normal: null,
        observed_region: [],
      },
    })]);
    const entry = reviewEntriesFor(scene, "outlet")[0];
    expect(entry.source).toBe("node");
    if (entry.source !== "node") throw new Error("expected node entry");
    const renderable = firstRenderableCrop(entry.observations);
    expect(renderable).toEqual(crop);
    expect(renderable?.image_url).toBe("crop-abc");
    expect(renderable?.provenance).toBe("manual");
  });

  it("observations without a crop image are never rendered as placeholder pixels", () => {
    const cropless: ObservationCrop = {
      frame_id: "frame-0001",
      sensor_box: [0, 0, 10, 10],
      confidence: 1,
      image_url: null,
      provenance: "manual",
      marked_by: null,
      marked_at: null,
      note: null,
    };
    expect(firstRenderableCrop([cropless])).toBeNull();
    expect(firstRenderableCrop([cropless, { ...cropless, sensor_box: [0, 0, 0, 5] }])).toBeNull();
  });
});
