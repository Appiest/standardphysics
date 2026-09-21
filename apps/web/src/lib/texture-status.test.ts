import { describe, expect, it } from "vitest";
import type { TextureStatus } from "@/types/contracts";
import { isTextureRefreshing, textureStatusMatches, textureStatusView } from "./texture-status";

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
