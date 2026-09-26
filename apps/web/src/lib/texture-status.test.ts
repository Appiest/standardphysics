import { describe, expect, it } from "vitest";
import type { TextureProgress, TextureStatus } from "@/types/contracts";
import { isTextureRefreshing, textureProgressView, textureStatusMatches, textureStatusView } from "./texture-status";

function status(state: TextureStatus["state"], canRetry = false): TextureStatus {
  return { state, can_retry: canRetry } as TextureStatus;
}

describe("textureStatusView", () => {
  it("does not offer a build when a capture has no synchronized photos", () => {
    expect(textureStatusView(status("needs_photos"))).toEqual({ message: "New photo capture needed", working: false, actionLabel: null });
  });

  it("offers a retry only when the failed build permits one", () => {
    expect(textureStatusView(status("failed"))).toEqual({ message: "Textures didn't finish", working: false, actionLabel: null });
    expect(textureStatusView(status("failed", true)).actionLabel).toBe("Try textures again");
  });

  it("makes completed photo coverage visible without treating it as a review result", () => {
    const complete = {
      ...status("complete"),
      build: { coverage: { textured_fraction: 0.214 } },
    } as TextureStatus;
    expect(textureStatusView(complete).message).toBe("Photos cover 21% of surfaces");
  });
});

describe("isTextureRefreshing", () => {
  it("refreshes while a build waits or runs, then stops at a terminal state", () => {
    expect(isTextureRefreshing("waiting_for_photos")).toBe(true);
    expect(isTextureRefreshing("queued")).toBe(true);
    expect(isTextureRefreshing("running")).toBe(true);
    expect(isTextureRefreshing("complete")).toBe(false);
  });
});

describe("textureStatusMatches", () => {
  it("keeps a late response from another revision out of the current model", () => {
    expect(textureStatusMatches({ ...status("complete"), scan_id: "scan-a", revision: 4 }, "scan-a", 4)).toBe(true);
    expect(textureStatusMatches({ ...status("complete"), scan_id: "scan-a", revision: 3 }, "scan-a", 4)).toBe(false);
    expect(textureStatusMatches({ ...status("complete"), scan_id: "scan-b", revision: 4 }, "scan-a", 4)).toBe(false);
  });
});

function progress(step: string, done: number | null, total: number | null, secondsIn: number): TextureProgress {
  const started = Date.parse("2026-09-25T20:00:00Z");
  return { step, done, total, step_started_at: new Date(started).toISOString(), reported_at: new Date(started + secondsIn * 1000).toISOString() };
}

describe("textureProgressView", () => {
  it("names the step and estimates the rest of it from the pace so far", () => {
    expect(textureProgressView(progress("people removal", 100, 400, 40))).toEqual({
      label: "Removing people", fraction: 0.25, timeLeft: "About 2 minutes left",
    });
  });

  it("shows no bar or estimate for a step that does not count what it works through", () => {
    expect(textureProgressView(progress("unwrap", null, null, 30))).toEqual({ label: "Preparing the surface", fraction: null, timeLeft: null });
  });

  it("waits for a few seconds of pace before estimating", () => {
    expect(textureProgressView(progress("photo bake", 2, 800, 1))?.timeLeft).toBeNull();
  });

  it("says less than a minute near the end and never counts past the whole", () => {
    expect(textureProgressView(progress("photo bake", 780, 800, 300))?.timeLeft).toBe("Less than a minute left");
    expect(textureProgressView(progress("photo bake", 900, 800, 300))).toMatchObject({ fraction: 1, timeLeft: null });
  });

  it("reads an unfamiliar step as the build as a whole", () => {
    expect(textureProgressView(progress("something new", null, null, 0))?.label).toBe("Adding photos");
  });

  it("has nothing to show before the build reports", () => {
    expect(textureProgressView(null)).toBeNull();
  });
});
