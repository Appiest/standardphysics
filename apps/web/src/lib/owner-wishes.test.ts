import { describe, expect, it } from "vitest";
import { keepChoices, withoutWish, withWishes } from "./owner-wishes";
import type { OwnerWish, ProposalResult, SceneGraph } from "@/types/contracts";

const scene = { nodes: [{ id: "chair", label: "Chair" }, { id: "table", label: "Cafe table" }] } as unknown as SceneGraph;
const nearTable: OwnerWish = { kind: "stays_near", node_id: "chair", anchor_id: "table", inches: 30, text: "Keep the chair at the cafe table" };
const move = { node_id: "chair", delta_translation: { x: 0.5, y: 0, z: 0 }, delta_rotation_z_degrees: 0 };

function result(bent: ProposalResult["explanation"]): ProposalResult {
  return { base_revision: 0, message: "", question: null, proposal: { moves: [move] }, explanation: bent } as unknown as ProposalResult;
}

describe("keepChoices", () => {
  it("offers what the proposal bent first, then keeping each moved piece where it is", () => {
    const choices = keepChoices(result({ moves: [], fixed: [], kept: [], bent: [{ text: "x", keep: nearTable }] }), scene);
    expect(choices.map((choice) => [choice.label, choice.bent])).toEqual([
      ["Keep the chair at the cafe table", true],
      ["Keep the chair where it is", false],
    ]);
  });

  it("skips a bent choice nothing single can keep, and works with no explanation", () => {
    expect(keepChoices(result({ moves: [], fixed: [], kept: [], bent: [{ text: "view", keep: null }] }), scene)).toHaveLength(1);
    expect(keepChoices(result(null), scene)).toHaveLength(1);
  });
});

it("numbers two choices that would otherwise read the same", () => {
  const twins = { ...scene, nodes: [...scene.nodes, { id: "chair-2", label: "Chair" }] } as unknown as SceneGraph;
  const second = { ...move, node_id: "chair-2" };
  const both = { ...result(null), proposal: { moves: [move, second] } } as unknown as ProposalResult;
  expect(keepChoices(both, twins).map((choice) => choice.label)).toEqual([
    "Keep the chair where it is (1 of 2)",
    "Keep the chair where it is (2 of 2)",
  ]);
});

describe("withWishes and withoutWish", () => {
  it("adds each wish once and removes the one asked", () => {
    const both = withWishes([nearTable], [nearTable, { ...nearTable, kind: "stays_put", anchor_id: null, inches: null }]);
    expect(both).toHaveLength(2);
    expect(withoutWish(both, nearTable)).toHaveLength(1);
  });
});
