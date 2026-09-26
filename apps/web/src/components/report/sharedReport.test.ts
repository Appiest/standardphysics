import { describe, expect, it } from "vitest";
import { headlineAndRest, readSharedReport } from "./sharedReport";

const json = (body: unknown, status: number) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

describe("readSharedReport", () => {
  it("hands back the report when the link works", async () => {
    const report = { scan: { name: "Sample boba shop" }, preview: false, rules: [], assessment: null, scenario: null, scene: null };
    expect(await readSharedReport(json(report, 200))).toEqual({ kind: "report", report });
  });

  it("keeps the API's words when the link has expired", async () => {
    const expired = json({ error: "This link has expired. Ask the shop for a new one.", need: null }, 404);
    expect(await readSharedReport(expired)).toEqual({ kind: "gone", message: "This link has expired. Ask the shop for a new one." });
  });

  it("still says the link has expired when a 404 carries no reason", async () => {
    const bare = new Response("not json", { status: 404 });
    expect(await readSharedReport(bare)).toEqual({ kind: "gone", message: "This link has expired. Ask the shop for a new one." });
  });

  it("lets a server failure reach the error page instead of calling it expired", async () => {
    await expect(readSharedReport(json({ error: "boom", need: null }, 500))).rejects.toThrow("500");
  });
});

describe("headlineAndRest", () => {
  it("splits the first sentence off as a heading without its full stop", () => {
    expect(headlineAndRest("This link has expired. Ask the shop for a new one.")).toEqual({
      headline: "This link has expired",
      rest: "Ask the shop for a new one.",
    });
  });

  it("keeps a single sentence whole", () => {
    expect(headlineAndRest("The example shop isn't here right now.")).toEqual({
      headline: "The example shop isn't here right now",
      rest: "",
    });
  });
});
