import { describe, expect, it } from "vitest";
import { blockedSentence } from "./blocked-copy";

describe("blockedSentence", () => {
  it("names both things in a collision", () => {
    expect(blockedSentence({ node_id: "a", reason: "collided", detail: "Display case into Wall" })).toBe(
      "The display case overlaps the wall.",
    );
  });

  it("says a fixed piece stays put", () => {
    expect(blockedSentence({ node_id: "a", reason: "moved_something_fixed", detail: "Ordering counter" })).toBe(
      "The ordering counter stays where it is.",
    );
  });

  it("says how far a piece may travel", () => {
    expect(blockedSentence({ node_id: "a", reason: "moved_too_far", detail: "Display case" })).toBe(
      "The display case can move up to 5 feet from where it was scanned.",
    );
  });

  it("says a table needs room to pull up to", () => {
    expect(blockedSentence({ node_id: "a", reason: "no_room_to_use", detail: "Table" })).toBe(
      "The table needs open floor on one side so someone can pull up to it.",
    );
  });
});
