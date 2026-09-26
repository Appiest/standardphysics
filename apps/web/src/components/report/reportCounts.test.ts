import { describe, expect, it } from "vitest";
import type { Finding, ReviewedRule } from "@/types/contracts";
import { beingCheckedNames, isBeingChecked, splitQuestions } from "./reportCounts";

const SENT = "We have your photo";

const citation = { authority: "ADA_2010", edition: "2010 ADA Standards", section: "404.2.7", url: null } as const;

function question(id: string, title: string, asks: Finding["asks"] = "photo"): Finding {
  return {
    id, check_id: id, outcome: "question", title, detail: "", fix: null, asks,
    measured_inches: null, required_inches: null, citation, locus: null,
  };
}

describe("splitQuestions", () => {
  it("leaves a photo the owner already sent out of what is still to send", () => {
    const handle = question("door_hardware", SENT, "review");
    const doorway = question("door_clear_width", "Measure the front doorway and send us the number", "measurement");
    const floor = question("floor_surface", "Send a photo of the floor just inside the front door");

    const { toSend, beingChecked } = splitQuestions([handle, doorway, floor]);

    expect(toSend.map((finding) => finding.id)).toEqual(["door_clear_width", "floor_surface"]);
    expect(beingChecked.map((finding) => finding.id)).toEqual(["door_hardware"]);
  });

  it("counts every question as still to send before any photo arrives", () => {
    const questions = [question("a", "Send a photo of the front door handle"), question("b", "Send a photo of the front doorway from the side")];
    expect(splitQuestions(questions)).toEqual({ toSend: questions, beingChecked: [] });
  });
});

describe("isBeingChecked", () => {
  it("only marks a question, never a result that happens to share the title", () => {
    const result = { ...question("a", SENT, "review"), outcome: "passes" as const };
    expect(isBeingChecked(result)).toBe(false);
  });
});

describe("beingCheckedNames", () => {
  it("names each photo by its rule, so the reader can tell which ones are in", () => {
    const rules = [{ check: { id: "door_hardware", title: "The front door handle" } }, { check: { id: "floor_surface", title: "The floor and the mats" } }] as ReviewedRule[];
    const sent = [question("door_hardware", SENT, "review"), question("floor_surface", SENT, "review")];
    expect(beingCheckedNames(sent, rules)).toEqual(["The front door handle", "The floor and the mats"]);
  });

  it("falls back to the rule's section when the rule isn't listed", () => {
    expect(beingCheckedNames([question("door_hardware", SENT, "review")], [])).toEqual(["404.2.7"]);
  });
});
