import { afterEach, describe, expect, it, vi } from "vitest";
import { cropUrl, frameUrl, getEvidence, isPlausibleSensorBox, markObservation, reviewAttachment } from "@/lib/review-client";

afterEach(() => vi.restoreAllMocks());

describe("review-client", () => {
  it("cropUrl points at the owner-authenticated crop route and escapes the id", () => {
    expect(cropUrl("scan-1", "f-123-abcdef01")).toBe("/api/scans/scan-1/crops/f-123-abcdef01");
    const trapped = cropUrl("scan-1", "../etc");
    expect(trapped).not.toContain("/../");
    expect(trapped.startsWith("/api/scans/scan-1/crops/")).toBe(true);
  });

  it("frameUrl uses the frames route for original photo marking", () => {
    expect(frameUrl("scan-1", "frame-42")).toBe("/api/scans/scan-1/frames/frame-42");
  });

  it("a manual mark posts the frozen ManualMarkRequest shape to the observations route", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue({ ok: true, json: async () => ({ revision: 1 }) } as Response);
    await markObservation("scan-1", 3, {
      target_class: "television",
      frame_id: "frame-9",
      sensor_box: [10, 20, 90, 80],
      node_id: "node-5",
      review_status: "candidate",
    });
    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/scans/scan-1/revisions/3/observations",
      expect.objectContaining({ method: "PUT", headers: { "Content-Type": "application/json" } })
    );
    const body = JSON.parse((fetchSpy.mock.calls[0][1] as RequestInit).body as string);
    expect(body).toEqual({
      target_class: "television",
      frame_id: "frame-9",
      node_id: "node-5",
      sensor_box: [10, 20, 90, 80],
      note: null,
      review_status: "candidate",
    });
  });

  it("reviews ride the attachment review route with the explicit status", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue({ ok: true, json: async () => ({ revision: 2 }) } as Response);
    await reviewAttachment("scan-1", 1, "node-7", "rejected_by_user");
    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/scans/scan-1/revisions/1/outlets/node-7/review",
      expect.objectContaining({ method: "PUT", body: JSON.stringify({ status: "rejected_by_user" }) })
    );
  });

  it("a 409 conflict surfaces as an explicit refusal, not a silent drop", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({ error: "this revision is no longer current" }),
    } as Response);
    await expect(reviewAttachment("scan-1", 1, "node-7", "confirmed_by_user"))
      .rejects.toMatchObject({ status: 409, message: "this revision is no longer current" });
  });

  it("getEvidence parses the evidence status and treats not-ready as null", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce({ ok: true, json: async () => ({ scan_id: "scan-1", evidence_state: "complete" }) } as Response)
      .mockResolvedValueOnce({ ok: false, status: 404, json: async () => ({}) } as Response);
    expect(await getEvidence("scan-1")).toEqual({ scan_id: "scan-1", evidence_state: "complete" });
    expect(await getEvidence("scan-1")).toBeNull();
  });

  it("only a positive rectangle counts as a markable sensor box", () => {
    expect(isPlausibleSensorBox([0, 0, 10, 10])).toBe(true);
    expect(isPlausibleSensorBox([10, 10, 0, 0])).toBe(false);
    expect(isPlausibleSensorBox([0, 0, 0, 10])).toBe(false);
    expect(isPlausibleSensorBox([0, 0, 10])).toBe(false);
    expect(isPlausibleSensorBox([NaN, 0, 10, 10])).toBe(false);
  });
});
