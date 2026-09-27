import { describe, expect, it } from "vitest";
import type { Checklist, Finding, Journey, OwnerRequest, SceneGraph, SceneNode } from "@/types/contracts";
import { checklistRows, comparisonBars, followUps, isFixing, isWaiting, openInShop, panelFor, pieceToTry, requestsForStep, thingsToFix, wheelchairStartFrom } from "./owner-journey";

const journey = (kind: Journey["next_step"]["kind"]): Journey => ({
  scan_id: "s", shop_name: "Corner cafe", stage: "fill_in_the_gaps", tools_unlocked: false,
  next_step: { kind, title: "", count: null },
});

const request = (id: string, changes: Partial<OwnerRequest> = {}): OwnerRequest => ({
  id, kind: "photo", timing: "in_shop", title: id, detail: "", unit: null, status: "open",
  answer: null, finding_id: null, review: null, ...changes,
});

describe("the owner view", () => {
  it("shows the panel the next step needs", () => {
    expect(panelFor(journey("photos"))).toBe("answers");
    expect(panelFor(journey("counter"))).toBe("counter");
    expect(panelFor(journey("checklist"))).toBe("results");
    expect(panelFor(journey("measuring"))).toBe("waiting");
  });

  it("polls only while the server is still working", () => {
    expect(isWaiting(journey("measuring"))).toBe(true);
    expect(isWaiting(journey("counter"))).toBe(false);
  });

  it("asks only the in-shop requests still open", () => {
    const requests = [request("a"), request("b", { status: "skipped" }), request("c", { timing: "follow_up" }), request("d", { kind: "another_look" })];
    expect(openInShop(requests).map((r) => r.id)).toEqual(["a"]);
    expect(followUps([request("e", { timing: "follow_up", status: "not_applicable" }), request("f", { timing: "follow_up" })]).map((r) => r.id)).toEqual(["f"]);
  });

  it("gives every problem a status, To do when the owner hasn't set one", () => {
    const problems = [{ id: "x" }, { id: "y" }] as Finding[];
    const checklist: Checklist = { items: [{ finding_id: "y", status: "done", updated_at: null }], done: 1, total: 2 };
    expect(checklistRows(problems, checklist).map((row) => row.status)).toEqual(["to_do", "done"]);
    expect(isFixing(checklist, false)).toBe(true);
    expect(isFixing({ items: [], done: 0, total: 2 }, false)).toBe(false);
  });

  it("counts the things to fix in words", () => {
    expect([0, 1, 3].map(thingsToFix)).toEqual(["Nothing to fix", "1 thing to fix", "3 things to fix"]);
  });

  it("scales both bars against the longer one", () => {
    expect(comparisonBars(47, 36)).toEqual({ measured: 1, required: 36 / 47 });
    expect(comparisonBars(31, 36)).toEqual({ measured: 31 / 36, required: 1 });
  });
});

describe("the in-shop step", () => {
  const all = [
    request("restroom", { kind: "yes_no" }), request("door_hardware"), request("door_opening_force", { kind: "number", unit: "lb" }),
  ];
  it("asks the yes or no questions first, then the photos, then the number", () => {
    expect(requestsForStep(journey("answers"), all).map((r) => r.id)).toEqual(["restroom"]);
    expect(requestsForStep(journey("photos"), all).map((r) => r.id)).toEqual(["door_hardware"]);
    expect(requestsForStep(journey("answers"), all.slice(1)).map((r) => r.id)).toEqual(["door_opening_force"]);
  });
});

describe("the wheelchair walk-through", () => {
  it("starts at the front door facing the counter", () => {
    const stop = (name: string, x: number, y: number) => ({ name, position: { x, y, z: 0 }, anchor_node_id: null });
    const start = wheelchairStartFrom({ name: "Customer path", stops: [stop("Entrance", 0, 0), stop("Counter", 0, 4)] });
    expect(start?.position).toEqual([0, 0, -0]);
    const facing = [-Math.sin(start!.yaw), -Math.cos(start!.yaw)];
    expect(facing[0]).toBeCloseTo(0);
    expect(facing[1]).toBeCloseTo(-1);
    expect(wheelchairStartFrom(null)).toBeNull();
  });
});

describe("the first thing to try in Plan a layout", () => {
  it("is a piece that can move, standing where a layout problem is", () => {
    const node = (id: string, movable: boolean) => ({ id, movable, label: id }) as unknown as SceneNode;
    const scene = { nodes: [node("counter", false), node("display case", true)] } as unknown as SceneGraph;
    const finding = (check_id: string, ids: string[]) => ({ check_id, locus: { node_ids: ids } }) as unknown as Finding;
    expect(pieceToTry([finding("service_counter_height", ["counter"]), finding("route_clear_width", ["counter", "display case"])], scene)?.label).toBe("display case");
    expect(pieceToTry([finding("service_counter_height", ["display case"])], scene)).toBeNull();
  });
});
