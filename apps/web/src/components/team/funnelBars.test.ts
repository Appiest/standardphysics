import { describe, expect, it } from "vitest";
import { duration, funnelBars } from "./funnelBars";

describe("the owner funnel", () => {
  it("scales each step against the shops that started and counts what each step lost", () => {
    const bars = funnelBars([
      { key: "walked", label: "Walked a shop", shops: 10 },
      { key: "uploaded", label: "Finished the upload", shops: 8 },
      { key: "results", label: "Saw results", shops: 8 },
    ]);
    expect(bars.map((bar) => [bar.share, bar.lost])).toEqual([[1, 0], [0.8, 2], [0.8, 0]]);
  });

  it("copes with nobody having started", () => {
    expect(funnelBars([{ key: "walked", label: "Walked a shop", shops: 0 }])[0].share).toBe(0);
  });

  it("says a time in words, or that there isn't one yet", () => {
    expect(duration(12.5, "minutes")).toBe("12.5 minutes");
    expect(duration(1, "hours")).toBe("1 hour");
    expect(duration(null, "hours")).toBe("Not yet");
  });
});
