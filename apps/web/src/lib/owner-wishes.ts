import type { OwnerWish, ProposalResult, SceneGraph } from "@/types/contracts";

export type KeepChoice = { key: string; wish: OwnerWish; label: string; bent: boolean };

export function wishKey(wish: OwnerWish): string {
  return `${wish.kind}:${wish.node_id}:${wish.anchor_id ?? ""}`;
}

function stayPut(scene: SceneGraph, nodeId: string): OwnerWish {
  const label = scene.nodes.find((node) => node.id === nodeId)?.label.toLowerCase() ?? "piece";
  return { kind: "stays_put", node_id: nodeId, anchor_id: null, inches: null, text: `Keep the ${label} where it is` };
}

/** Twin labels numbered "(1 of 2)", so two choices about two display cases never read the same. */
function numberTwins(choices: KeepChoice[]): KeepChoice[] {
  const totals = new Map<string, number>();
  for (const choice of choices) totals.set(choice.label, (totals.get(choice.label) ?? 0) + 1);
  const seen = new Map<string, number>();
  return choices.map((choice) => {
    const total = totals.get(choice.label) ?? 1;
    if (total === 1) return choice;
    const nth = (seen.get(choice.label) ?? 0) + 1;
    seen.set(choice.label, nth);
    return { ...choice, label: `${choice.label} (${nth} of ${total})` };
  });
}

/** What the owner can ask to keep after turning a proposal down: what it bent first, then each piece it moved. */
export function keepChoices(result: ProposalResult, scene: SceneGraph): KeepChoice[] {
  const bent = (result.explanation?.bent ?? []).flatMap((item) => (item.keep ? [{ wish: item.keep, bent: true }] : []));
  const moved = (result.proposal?.moves ?? []).map((move) => ({ wish: stayPut(scene, move.node_id), bent: false }));
  const seen = new Set<string>();
  return numberTwins([...bent, ...moved].flatMap(({ wish, bent: wasBent }) => {
    const key = wishKey(wish);
    if (seen.has(key)) return [];
    seen.add(key);
    return [{ key, wish, label: wish.text, bent: wasBent }];
  }));
}

/** The saved wishes with new ones added, each kept once. */
export function withWishes(saved: OwnerWish[], added: OwnerWish[]): OwnerWish[] {
  const byKey = new Map(saved.map((wish) => [wishKey(wish), wish]));
  for (const wish of added) byKey.set(wishKey(wish), wish);
  return [...byKey.values()];
}

export function withoutWish(saved: OwnerWish[], removed: OwnerWish): OwnerWish[] {
  return saved.filter((wish) => wishKey(wish) !== wishKey(removed));
}
