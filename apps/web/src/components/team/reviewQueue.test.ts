import { describe, expect, it } from "vitest";
import type { PendingReview } from "@/types/contracts";
import { decisionMessage, photosToCheckHeading, reviewKey, reviewPath, stillWaiting } from "./reviewQueue";

function review(scanId: string, requestId: string, shopName = "Sample boba shop"): PendingReview {
  return {
    scan_id: scanId,
    shop_name: shopName,
    request: {
      id: requestId, kind: "photo", timing: "in_shop", title: "Send a photo of the front door handle", detail: "",
      unit: null, status: "answered", finding_id: null, review: null,
      answer: { yes: null, number: null, photo_url: `/api/team/reviews/${scanId}/${requestId}/photo`, answered_at: "2026-09-26T10:58:06Z" },
    },
  };
}

describe("stillWaiting", () => {
  it("drops the photos already decided and keeps the rest in the queue's order", () => {
    const [handle, floor, doorway] = [review("s1", "door_hardware"), review("s1", "floor_surface"), review("s2", "door_hardware")];
    expect(stillWaiting([handle, floor, doorway], new Set([reviewKey(handle)]))).toEqual([floor, doorway]);
  });

  it("tells the same request apart in two shops", () => {
    expect(reviewKey(review("s1", "door_hardware"))).not.toBe(reviewKey(review("s2", "door_hardware")));
  });
});

describe("reviewPath", () => {
  it("points at the team's review route for that shop and request", () => {
    expect(reviewPath(review("f68aa174", "door_hardware"))).toBe("/api/team/reviews/f68aa174/door_hardware");
  });
});

describe("photosToCheckHeading", () => {
  it("counts the photos in words that read right for none, one and many", () => {
    expect([0, 1, 3].map(photosToCheckHeading)).toEqual(["No photos to check", "1 photo to check", "3 photos to check"]);
  });
});

describe("decisionMessage", () => {
  it("names the shop and the outcome", () => {
    expect(decisionMessage(review("s1", "a", "Lemon Tea Bar"), "problem")).toBe("Marked the photo from Lemon Tea Bar as a problem.");
    expect(decisionMessage(review("s1", "a"), "passes")).toBe("Marked the photo from Sample boba shop as passing.");
  });
});
