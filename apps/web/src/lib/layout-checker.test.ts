import { describe, expect, it } from "vitest";
import type { LayoutCheckResult, NodeMove } from "@/types/contracts";
import { type Checked, LayoutChecker, layoutKey } from "./layout-checker";
import type { MoveSet } from "./moves";

function move(nodeId: string, x: number): NodeMove {
  return { node_id: nodeId, delta_translation: { x, y: 0, z: 0 }, delta_rotation_z_degrees: 0 };
}

function layout(x: number): MoveSet {
  return { chair: move("chair", x) };
}

function result(sequence: number): LayoutCheckResult {
  return { sequence, graph_hash: `hash-${sequence}`, findings: [], blocked: [] };
}

/** A server that answers only when told to, so the test decides what is still on its way. */
function slowServer() {
  const asked: { moves: NodeMove[]; answer: () => void; fail: () => void }[] = [];
  const run = (moves: NodeMove[], sequence: number) => new Promise<LayoutCheckResult>((resolve, reject) => {
    asked.push({ moves, answer: () => resolve(result(sequence)), fail: () => reject(new Error("down")) });
  });
  return { asked, run };
}

function recorder() {
  const results: Checked[] = [];
  const errors: MoveSet[] = [];
  const busy: boolean[] = [];
  return { results, errors, busy, listeners: { onResult: (c: Checked) => results.push(c), onError: (m: MoveSet) => errors.push(m), onBusy: (b: boolean) => busy.push(b) } };
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("layoutKey", () => {
  it("is the same for the same moves in any order and for float noise under a millimetre", () => {
    const a = { chair: move("chair", 0.1), table: move("table", 0.2) };
    const b = { table: move("table", 0.2000001), chair: move("chair", 0.1) };
    expect(layoutKey(a)).toBe(layoutKey(b));
    expect(layoutKey(a)).not.toBe(layoutKey({ chair: move("chair", 0.102), table: move("table", 0.2) }));
  });
});

describe("LayoutChecker", () => {
  it("keeps one check out at a time and follows it with only the newest layout asked for", async () => {
    const server = slowServer();
    const seen = recorder();
    const checker = new LayoutChecker(server.run, seen.listeners);
    checker.request(layout(0.1));
    checker.request(layout(0.2));
    checker.request(layout(0.3));
    expect(server.asked).toHaveLength(1);
    server.asked[0].answer();
    await settle();
    expect(server.asked).toHaveLength(2);
    expect(server.asked[1].moves[0].delta_translation.x).toBe(0.3);
    server.asked[1].answer();
    await settle();
    expect(seen.results.map((checked) => checked.moves.chair.delta_translation.x)).toEqual([0.1, 0.3]);
    expect(seen.busy).toEqual([true, true, false]);
  });

  it("answers a layout it already checked from its cache, without asking the server", async () => {
    const server = slowServer();
    const seen = recorder();
    const checker = new LayoutChecker(server.run, seen.listeners);
    checker.request(layout(0.1));
    server.asked[0].answer();
    await settle();
    checker.request(layout(0.1));
    expect(server.asked).toHaveLength(1);
    expect(seen.results.at(-1)?.milliseconds).toBe(0);
  });

  it("drops the answer to a check cancelled while it was out, and anything queued behind it", async () => {
    const server = slowServer();
    const seen = recorder();
    const checker = new LayoutChecker(server.run, seen.listeners);
    checker.request(layout(0.1));
    checker.request(layout(0.2));
    checker.cancel();
    server.asked[0].answer();
    await settle();
    expect(seen.results).toEqual([]);
    expect(server.asked).toHaveLength(1);
    expect(checker.cached(layout(0.1))).toBeDefined();
  });

  it("reports a failed check and still sends the layout queued behind it", async () => {
    const server = slowServer();
    const seen = recorder();
    const checker = new LayoutChecker(server.run, seen.listeners);
    checker.request(layout(0.1));
    checker.request(layout(0.2));
    server.asked[0].fail();
    await settle();
    expect(seen.errors).toHaveLength(1);
    expect(server.asked).toHaveLength(2);
  });
});
