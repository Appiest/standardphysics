import { describe, expect, it } from "vitest";
import { evidenceLanes, openEvidenceNotes, type LaneView } from "@/lib/evidence";
import type { EvidenceStatus, TextureStatus } from "@/types/contracts";

const evidence = (overrides: Partial<EvidenceStatus> = {}): EvidenceStatus => ({
  scan_id: "scan-1",
  bundle_version: 1,
  complete_evidence: false,
  evidence_state: "partial",
  geometry_state: "ready",
  latest_bundle: null,
  manifest_hash: null,
  missing_geometry_kinds: [],
  missing_semantic_kinds: [],
  present_kinds: [],
  reasons: [],
  semantic_job_pending: false,
  semantic_state: "not_started",
  ...overrides,
});

const textures = (overrides: Partial<TextureStatus> = {}): TextureStatus => ({
  scan_id: "scan-1",
  revision: 0,
  exact: true,
  stale_node_ids: [],
  error: null,
  can_retry: true,
  state: "complete",
  build: null,
  progress: null,
  ...overrides,
});

function lane(lanes: LaneView[], id: string): LaneView {
  const found = lanes.find((candidate) => candidate.id === id);
  expect(found).toBeDefined();
  return found!;
}

describe("evidence lanes", () => {
  it("keeps the four processing lanes separate and all complete reads all-done", () => {
    const view = evidenceLanes(
      evidence({ complete_evidence: true, semantic_state: "complete", geometry_state: "ready" }),
      textures(),
      true
    );
    expect(view.lanes.map((candidate) => candidate.id)).toEqual(["geometry", "evidence", "recognition", "visual"]);
    expect(view.allDone).toBe(true);
  });

  it("a scan with no evidence status yet never claims a finished room", () => {
    const view = evidenceLanes(null, null, false);
    expect(view.allDone).toBe(false);
    expect(lane(view.lanes, "geometry").tone).toBe("needs_action");
    expect(lane(view.lanes, "visual").detail).toContain("Measurements work without it");
  });

  it("missing semantic evidence names what to recapture, not a blanket ready state", () => {
    const view = evidenceLanes(
      evidence({ evidence_state: "incomplete", missing_semantic_kinds: ["poses", "frames"], semantic_state: "blocked_incomplete_evidence" }),
      textures(),
      true
    );
    const recognition = lane(view.lanes, "recognition");
    expect(recognition.tone).toBe("needs_action");
    expect(recognition.detail).toContain("poses");
    expect(recognition.detail).toContain("frames");
    expect(lane(view.lanes, "evidence").headline).toBe("Photo evidence incomplete");
  });

  it("failed recognition offers a retry and explains that nothing was reported", () => {
    const view = evidenceLanes(evidence({ semantic_state: "failed" }), textures(), true);
    const recognition = lane(view.lanes, "recognition");
    expect(recognition.tone).toBe("failed");
    expect(recognition.action).toEqual({ kind: "retry", label: "Retry photo recognition" });
    expect(openEvidenceNotes(view.lanes).length).toBe(1);
  });

  it("working lanes are named as working and never counted against the summary quickly", () => {
    const view = evidenceLanes(evidence({ semantic_state: "running" }), textures({ state: "running" }), true);
    expect(lane(view.lanes, "recognition").tone).toBe("working");
    expect(lane(view.lanes, "visual").tone).toBe("working");
    expect(view.allDone).toBe(false);
  });

  it("failed textures keep the measured plan usable message", () => {
    const view = evidenceLanes(evidence(), textures({ state: "failed", can_retry: false }), true);
    const visual = lane(view.lanes, "visual");
    expect(visual.tone).toBe("failed");
    expect(visual.detail).toContain("measured plan and photos stay usable");
    expect(visual.action).toBeNull();
    expect(openEvidenceNotes(view.lanes).some((note) => note.includes("measured plan and photos stay usable"))).toBe(true);
  });
});
