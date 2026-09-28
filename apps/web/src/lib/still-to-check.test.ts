import { describe, expect, it } from "vitest";
import type { Finding, OwnerRequest } from "@/types/contracts";
import { onePerTitle, stillToCheckCount, stillToCheckItems } from "./still-to-check";

const question = (id: string, title: string) => ({ id, title, outcome: "question" }) as unknown as Finding;
const request = (id: string, findingId: string, changes: Partial<OwnerRequest> = {}): OwnerRequest => ({
  id, kind: "number", timing: "follow_up", title: id, detail: "", unit: "in", status: "open",
  answer: null, finding_id: findingId, review: null, ...changes,
});

describe("what is still to check", () => {
  it("shows one look-again card per spot, however many checks rest on it", () => {
    const questions = [
      question("a", "Point the phone at the ordering counter again"),
      question("b", "Point the phone at the ordering counter again"),
      question("c", "Point the phone at the two chairs again"),
    ];
    const items = stillToCheckItems(questions, []);
    expect(items.lookAgain.map((finding) => finding.title)).toEqual([
      "Point the phone at the ordering counter again",
      "Point the phone at the two chairs again",
    ]);
    expect(stillToCheckCount(items)).toBe(2);
  });

  it("counts every sendable request and what is left to look at, and nothing twice", () => {
    const questions = [question("door-1", "Measure door 1"), question("door-2", "Measure door 2"), question("chair", "Point the phone at the chair again")];
    const requests = [request("r1", "door-1"), request("r2", "door-2"), request("r3", "chair", { kind: "another_look" })];
    const items = stillToCheckItems(questions, requests);
    expect(items.sendable.map((each) => each.id)).toEqual(["r1", "r2"]);
    expect(items.lookAgain.map((finding) => finding.id)).toEqual(["chair"]);
    expect(stillToCheckCount(items)).toBe(3);
  });

  it("does not offer a request that no longer applies", () => {
    const items = stillToCheckItems([question("q", "Send a photo of the restroom")], [request("r", "q", { status: "not_applicable" })]);
    expect(items.sendable).toEqual([]);
    expect(items.lookAgain.map((finding) => finding.id)).toEqual(["q"]);
  });

  it("keeps the first of each title, in order", () => {
    const cards = [{ title: "Point at the counter", id: 1 }, { title: "Point at the ramp", id: 2 }, { title: "Point at the counter", id: 3 }];
    expect(onePerTitle(cards).map((card) => card.id)).toEqual([1, 2]);
  });
});
