import { describe, expect, it } from "vitest";
import { foundInModel, showsFound, type FoundObjects } from "@/components/owner/useFoundObjects";
import type { SceneGraph, SceneNode } from "@/types/contracts";
import { foldedSummary, foundGroups, foundMarks, heightRange, rowCenter, rowLabel, type FoundRow } from "./found-objects";

type Piece = Pick<SceneNode, "id" | "kind" | "label" | "raw_category"> & { dimensions: [number, number, number]; m: SceneNode["transform"]["m"] };

function node({ id, kind, label, raw_category, dimensions: [x, y, z], m }: Piece): SceneNode {
  return {
    id, kind, label, raw_category, dimensions: { x, y, z }, transform: { m },
    movable: kind === "object", labeled_by: "discovery", parent_id: null, quality: "measured",
  };
}

/** Pieces as the Share-Tea scan placed them, rounded to the tenth of a millimetre. */
const SHARE_TEA: Piece[] = [
  { id: "wall", kind: "wall", label: "Wall", raw_category: "wall", dimensions: [9.6715, 0, 3.1867], m: [0.8859, 0.4638, 0, -0.5562, -0.4638, 0.8859, 0, 4.6289, 0, 0, 1, 1.5933, 0, 0, 0, 1] },
  { id: "floor", kind: "floor", label: "Floor", raw_category: "floor", dimensions: [5.7737, 0, 11.9862], m: [-0.4635, 0, 0.8861, -1.0859, -0.8861, 0, -0.4635, 1.7484, 0, -1, 0, 0, 0, 0, 0, 1] },
  { id: "counter-front", kind: "object", label: "Counter", raw_category: "storage", dimensions: [1.7104, 0.7439, 0.907], m: [0.4638, -0.8859, 0, -3.1997, 0.8859, 0.4638, 0, 5.0475, 0, 0, 1, 0.4535, 0, 0, 0, 1] },
  { id: "counter-side", kind: "object", label: "Counter", raw_category: "storage", dimensions: [1.724, 0.6743, 0.8724], m: [0.4759, -0.8795, 0, -4.3785, 0.8795, 0.4759, 0, 2.7585, 0, 0, 1, 0.4362, 0, 0, 0, 1] },
  { id: "chair", kind: "object", label: "Chair", raw_category: "chair", dimensions: [0.4767, 0.5498, 0.8702], m: [-0.9019, -0.432, 0, -0.0923, 0.432, -0.9019, 0, 0.8868, 0, 0, 1, 0.4351, 0, 0, 0, 1] },
  { id: "chair-in-the-air", kind: "object", label: "Chair", raw_category: "chair", dimensions: [0.2378, 0.277, 0.5858], m: [0.4568, -0.8896, 0, 2.3471, 0.8896, 0.4568, 0, 2.3165, 0, 0, 1, 0.8501, 0, 0, 0, 1] },
  { id: "chair-speck", kind: "object", label: "Chair", raw_category: "chair", dimensions: [0.0719, 0.0376, 0.1778], m: [0.3806, -0.9247, 0, 0.0581, 0.9247, 0.3806, 0, 3.4947, 0, 0, 1, 0.1361, 0, 0, 0, 1] },
  { id: "table", kind: "object", label: "Table", raw_category: "table", dimensions: [3.8011, 0.9361, 0.7372], m: [0.9019, 0.432, 0, 0.0743, -0.432, 0.9019, 0, 1.2346, 0, 0, 1, 0.3686, 0, 0, 0, 1] },
  { id: "terminal", kind: "object", label: "Payment terminal", raw_category: "payment_terminal", dimensions: [0.2658, 0.479, 0.4902], m: [0.2675, -0.9636, 0, -2.6535, 0.9636, 0.2675, 0, -0.3449, 0, 0, 1, 1.3022, 0, 0, 0, 1] },
  { id: "cup", kind: "object", label: "Cup", raw_category: "cup", dimensions: [0.1175, 0.153, 0.147], m: [0.5163, -0.8564, 0, -0.85, 0.8564, 0.5163, 0, -0.9336, 0, 0, 1, 1.1737, 0, 0, 0, 1] },
  { id: "extinguisher", kind: "object", label: "Fire extinguisher", raw_category: "fire_extinguisher", dimensions: [0.1375, 0.071, 0.45], m: [0.7951, -0.6065, 0, 3.8322, 0.6065, 0.7951, 0, 1.2491, 0, 0, 1, 1.0522, 0, 0, 0, 1] },
  { id: "outlet", kind: "outlet", label: "Outlet (outlet)", raw_category: "outlet", dimensions: [0.12, 0.03, 0.12], m: [-0.9019, -0.432, 0, 4.5767, 0.432, -0.9019, 0, 1.9103, 0, 0, -1, 0.4852, 0, 0, 0, 1] },
];

const scene: SceneGraph = { scan_id: "share-tea", revision: 1, base_hash: null, nodes: SHARE_TEA.map(node) };

function rowNamed(name: string): FoundRow | undefined {
  return foundGroups(scene).flatMap((group) => group.rows).find((row) => row.name === name);
}

describe("foundGroups", () => {
  it("groups what an owner cares about in a fixed order", () => {
    const groups = foundGroups(scene).map((group) => [group.title, group.rows.map(rowLabel)]);
    expect(groups).toEqual([
      ["Counters and payment", ["2 counters", "Payment terminal"]],
      ["Tables and seating", ["Chair", "Table"]],
      ["Safety", ["Fire extinguisher"]],
    ]);
  });

  it("leaves out structure, outlets, loose cups, specks and chairs hanging in the air", () => {
    const listed = foundGroups(scene).flatMap((group) => group.rows.flatMap((row) => row.nodeIds));
    for (const skipped of ["wall", "floor", "outlet", "cup", "chair-speck", "chair-in-the-air"]) expect(listed).not.toContain(skipped);
  });

  it("measures counter tops from the floor, which is the height the ADA sales-counter check uses", () => {
    expect(heightRange(rowNamed("Counter")?.topInches ?? [])).toBe("34.3–35.7 in");
  });

  it("gives no height for kinds the checks don't measure by height", () => {
    expect(rowNamed("Chair")?.topInches).toEqual([]);
    expect(rowNamed("Table")?.topInches.map(Math.round)).toEqual([29]);
  });

  it("keeps a service counter the owner marked as its own row", () => {
    const marked = { ...scene, nodes: [...scene.nodes, { ...node(SHARE_TEA[2]), id: "marked", label: "service counter", labeled_by: "owner" }] };
    const names = foundGroups(marked)[0].rows.map((row) => row.name);
    expect(names).toContain("Service counter");
  });
});

describe("foundMarks", () => {
  it("gives each piece its own height, so the 3D label says which counter is which", () => {
    const counters = foundMarks(scene, foundGroups(scene)).filter((mark) => mark.name === "Counter");
    expect(counters.map((mark) => [mark.nodeId, heightRange([mark.topInches ?? 0])])).toEqual([
      ["counter-front", "35.7 in"],
      ["counter-side", "34.3 in"],
    ]);
  });
});

describe("row wording", () => {
  it("pluralises the way people say it", () => {
    const row = (name: string, count: number): FoundRow => ({ id: name, group: "other", name, nodeIds: Array.from({ length: count }, (_, index) => `${name}-${index}`), topInches: [] });
    expect([rowLabel(row("Bench", 2)), rowLabel(row("Display stand", 3)), rowLabel(row("Shelf", 1)), rowLabel(row("Gallery", 2))]).toEqual(["2 benches", "3 display stands", "Shelf", "2 galleries"]);
  });

  it("names a folded list by its first two kinds", () => {
    const rows = ["Sign", "Screen", "Column", "Fridge"].map((name, index): FoundRow => ({ id: name, group: "other", name, nodeIds: index === 0 ? ["a", "b"] : ["c"], topInches: [] }));
    expect(foldedSummary(rows)).toBe("signs, screen and 2 more");
    expect(foldedSummary(rows.slice(0, 2))).toBe("signs and screen");
  });

  it("shows one height when the pieces agree", () => {
    expect(heightRange([35.71, 35.74])).toBe("35.7 in");
    expect(heightRange([])).toBeNull();
  });
});

describe("rowCenter", () => {
  it("is the middle of the row's pieces, for the camera to face", () => {
    const counters = rowNamed("Counter") as FoundRow;
    const center = rowCenter(scene, counters);
    expect(center?.x).toBeCloseTo((-3.1997 + -4.3785) / 2);
    expect(center?.y).toBeCloseTo((5.0475 + 2.7585) / 2);
  });
});

describe("where the found pieces show", () => {
  const found = { handles: { marks: [] }, focus: { x: 1, y: 2, z: 0 }, frameShift: 168 } as unknown as FoundObjects;

  it("shows them on the steps that read the shop, not the ones that pick, drag or drive", () => {
    expect(["answers", "follow_ups", "results"].every(showsFound)).toBe(true);
    expect(["counter", "path", "plan", "wheelchair"].some(showsFound)).toBe(false);
  });

  it("hands the model nothing, and no shift, on a step that hides them", () => {
    expect(foundInModel(found, false)).toEqual({ found: null, foundFocus: null, frameShift: 0 });
    expect(foundInModel(found, true).frameShift).toBe(168);
  });
});
