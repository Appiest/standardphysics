/**
 * Generated from packages/contracts by `npm run contracts`. Do not edit.
 * Change the Pydantic models instead, then regenerate.
 */

/**
 * One line of `POST /api/scans/{scan_id}/loop/stream`, which reports each pass as it finishes.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "LoopEvent".
 */
export type LoopEvent = LoopStarted | LoopPassFinished | LoopFinished | LoopFailed;

export interface StandardPhysicsContracts {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "AdaptiveRoundResult".
 */
export interface AdaptiveRoundResult {
  accepted: boolean;
  astra_model: string | null;
  base_graph_hash: string;
  jev_preferred_candidate: string | null;
  reasons: string[];
  round: number;
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "Annotation".
 */
export interface Annotation {
  kind: "dimension_line" | "region" | "path";
  label: string;
  point_inches: (number | null)[] | null;
  points: Vec3[];
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "Vec3".
 */
export interface Vec3 {
  x: number;
  y: number;
  z: number;
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "ApiError".
 */
export interface ApiError {
  error: string;
  need: string[] | null;
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "Artifact".
 */
export interface Artifact {
  bytes: number;
  id: string;
  kind:
    | "room_usdz"
    | "room_json"
    | "room_metadata"
    | "walkthrough_mp4"
    | "frames"
    | "poses"
    | "coverage"
    | "lidar_mesh"
    | "photo_manifest";
  sha256: string;
  stored_path: string | null;
}
/**
 * Lane C's answer to a question about the shop, in wire form.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "AskAnswer".
 */
export interface AskAnswer {
  data: {
    [k: string]: unknown;
  };
  findings: Finding[];
  kind: string | null;
  locus: Locus | null;
  proposal: Proposal | null;
  subjects: string[];
  text: string;
  understood: boolean;
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "Finding".
 */
export interface Finding {
  check_id: string;
  citation: Citation;
  detail: string;
  fix: string | null;
  id: string;
  locus: Locus | null;
  measured_inches: number | null;
  outcome: "passes" | "problem" | "question";
  required_inches: number | null;
  title: string;
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "Citation".
 */
export interface Citation {
  authority: "ADA_2010" | "CBC" | "PAMC";
  edition: string;
  section: string;
  url: string | null;
}
/**
 * Where in the model this finding lives, and how to show it.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "Locus".
 */
export interface Locus {
  annotation: Annotation;
  bbox_max: Vec3;
  bbox_min: Vec3;
  camera: CameraPose;
  node_ids: string[];
  point: Vec3;
  render_url: string | null;
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "CameraPose".
 */
export interface CameraPose {
  fov_degrees: number;
  position: Vec3;
  target: Vec3;
}
/**
 * Movable nodes only. No resize, no fixture movement, no leaving the floor.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "Proposal".
 */
export interface Proposal {
  base_graph_hash: string;
  id: string;
  inventory_after: {
    [k: string]: number;
  };
  inventory_before: {
    [k: string]: number;
  };
  moves: NodeMove[];
  rationale: string;
  targets: string[];
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "NodeMove".
 */
export interface NodeMove {
  delta_rotation_z_degrees: number;
  delta_translation: Vec3;
  node_id: string;
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "AskRequest".
 */
export interface AskRequest {
  base_revision: number;
  text: string;
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "Assessment".
 */
export interface Assessment {
  created_at: string;
  decision: Decision | null;
  findings: Finding[];
  graph_hash: string;
  graph_revision: number;
  id: string;
  pass_number: number;
  rulepack_version: string;
  rules_checked: number | null;
  scan_id: string;
  scope: ScopeManifest | null;
  weave_run_url: string | null;
}
/**
 * TypeSafe's structured output, after validation.
 *
 * An action that fails validation authorizes nothing.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "Decision".
 */
export interface Decision {
  action: "FIX" | "RESCAN_AREA" | "ASK_OWNER" | "ESCALATE" | "DONE";
  provider: string;
  question: string | null;
  rationale: string | null;
  target_finding_ids: string[];
}
/**
 * The immutable, hashed list of everything this assessment was asked to cover.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "ScopeManifest".
 */
export interface ScopeManifest {
  applicability_questions: string[];
  created_at: string;
  graph_hash: string;
  graph_revision: number;
  id: string;
  manifest_hash: string;
  requested_classes: string[];
  requested_requirements: string[];
  route_endpoints: string[];
  rows: ScopeRow[];
  rulepack_version: string;
  scan_id: string;
  surveyed_areas: string[];
  unobserved_areas: string[];
  unresolved_questions: string[];
  version: number;
}
/**
 * One requested requirement applied to one item, with its outcome.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "ScopeRow".
 */
export interface ScopeRow {
  applicability: "applicable" | "not_applicable" | "unknown";
  applicability_facts: string[];
  applicability_reason: string | null;
  evidence_refs: string[];
  item: ScopeItem;
  legal_review_status: "unreviewed_preview" | "needs_review" | "reviewer_supplied";
  measurement: {
    [k: string]: unknown;
  } | null;
  outcome: "satisfied" | "violation" | "needs_verification" | "not_applicable" | "unobserved";
  reason: string | null;
  requested: boolean;
  requirement_id: string;
  source_version: string | null;
}
/**
 * One thing an outcome can be about: an object, an area, or a route leg.
 *
 * `item_id` names a SceneNode when the item was found. An unresolved class or
 * an unobserved area keeps a stable slug so the obligation survives every
 * later revision.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "ScopeItem".
 */
export interface ScopeItem {
  item_id: string | null;
  item_kind: "object" | "area" | "route" | "site" | "class";
  item_slug: string;
  label: string;
  observed: boolean;
  source: "measured" | "owner_confirmed" | "manual_photo" | "requested_not_observed";
}
/**
 * A hard constraint a layout breaks, from Lane C's fix constraints.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "Blocked".
 */
export interface Blocked {
  detail: string;
  node_id: string;
  reason: string;
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "Check".
 */
export interface Check {
  applies_to: string[];
  citation: Citation;
  id: string;
  threshold: number;
  tier: 1 | 2 | 3;
  title: string;
  unit: string;
  verified_by_human: boolean;
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "ClearFloorResult".
 */
export interface ClearFloorResult {
  center: Vec3;
  fits: boolean;
  inches_deep: number;
  inches_wide: number;
}
/**
 * The optional body of POST /complete.
 *
 * A legacy client sends no body at all and keeps its existing behavior. A
 * client that tracks its own uploads can declare what it believes it uploaded;
 * the server still decides readiness from stored bytes.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "CompleteRequest".
 */
export interface CompleteRequest {
  client_version: string | null;
  declared_complete: boolean;
  manifest_hash: string | null;
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "CreateScanRequest".
 */
export interface CreateScanRequest {
  device_model: string;
  duration_seconds: number;
  name: string;
}
/**
 * An inferred finish for rendering; never physical or compliance evidence.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "DisplayAppearance".
 */
export interface DisplayAppearance {
  base_color: string;
  material: "paint" | "wood" | "fabric" | "metal" | "stone" | "glass" | "neutral";
  source: "astra";
}
/**
 * A completed visual part inside the measured object's normalized bounds.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "DisplayPart".
 */
export interface DisplayPart {
  axis: "x" | "y" | "z";
  base_color: string;
  bevel: number;
  /**
   * @minItems 3
   * @maxItems 3
   */
  center: [number, number, number, ...number[]];
  material: "paint" | "wood" | "fabric" | "metal" | "stone" | "glass" | "neutral";
  name: string;
  primitive: "box" | "cylinder" | "ellipsoid";
  /**
   * @minItems 3
   * @maxItems 3
   */
  size: [number, number, number, ...number[]];
}
/**
 * Photo-informed completion. Never a recovered measurement or verified clearance.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "DisplayReconstruction".
 */
export interface DisplayReconstruction {
  confidence: number;
  /**
   * @minItems 1
   * @maxItems 6
   */
  evidence_frame_ids: [string, ...string[]];
  /**
   * @minItems 1
   * @maxItems 32
   */
  parts: [DisplayPart, ...DisplayPart[]];
  source: "astra";
  summary: string;
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "EnvironmentPhysicsResult".
 */
export interface EnvironmentPhysicsResult {
  cashiers_found: number;
  exits_found: number;
  limitations: string[];
  mesh_triangles_checked: number;
  observations: PhysicsObservation[];
  resolution_inches: number;
  routes: PhysicsRoute[];
  seats_found: number;
  surface_samples: number;
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "PhysicsObservation".
 */
export interface PhysicsObservation {
  kind: "surface_slope" | "uncontrolled_roll" | "wheelchair_tip" | "level_change" | "stair_or_step" | "turning";
  measured_value: number | null;
  node_ids: string[];
  point: Vec3 | null;
  reference_value: number | null;
  source: "lidar_mesh" | "scene_graph" | "route_geometry";
  status: "clear" | "potential_barrier" | "needs_measurement";
  title: string;
  unit: string | null;
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "PhysicsRoute".
 */
export interface PhysicsRoute {
  blocking_node_ids: string[];
  clear_width_inches: number | null;
  destination_node_id: string;
  distance_inches: number | null;
  origin_node_id: string;
  purpose: "customer_access" | "evacuation" | "seat_to_cashier";
  reachable: boolean;
}
/**
 * What a result was read off, and where to look to see it.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "Evidence".
 */
export interface Evidence {
  at: Vec3 | null;
  frames: string[];
  subjects: string[];
}
/**
 * One immutable closure of the artifacts a scan has at a point in time.
 *
 * Version 1 is written when the scan is first completed. Evidence that arrives
 * after the first closure becomes version 2, and so on; old bundles are kept.
 * `semantic_processed_hash` records the manifest a semantic job actually
 * consumed, which is how a changed bundle schedules exactly one new job.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "EvidenceBundle".
 */
export interface EvidenceBundle {
  artifact_hashes: {
    [k: string]: string;
  };
  artifact_ids: string[];
  complete: boolean;
  created_at: string | null;
  manifest_hash: string;
  missing_required_kinds: string[];
  reasons: string[];
  semantic_processed_hash: string | null;
  version: number;
}
/**
 * Where a scan is between raw upload and processed semantic evidence.
 *
 * The three states are separate on purpose: a room can have usable geometry
 * while photo recognition is still blocked, and "ready" on the legacy Scan
 * state must not be read as complete evidence.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "EvidenceStatus".
 */
export interface EvidenceStatus {
  bundle_version: number;
  complete_evidence: boolean;
  evidence_state: "awaiting" | "partial" | "complete" | "incomplete";
  geometry_state: "awaiting" | "ready" | "failed";
  latest_bundle: EvidenceBundle | null;
  manifest_hash: string | null;
  missing_geometry_kinds: string[];
  missing_semantic_kinds: string[];
  present_kinds: string[];
  reasons: string[];
  scan_id: string;
  semantic_job_pending: boolean;
  semantic_state: "not_started" | "blocked_incomplete_evidence" | "queued" | "running" | "complete" | "failed";
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "HeightResult".
 */
export interface HeightResult {
  inches: number;
  measured_at: Vec3;
  node_id: string;
}
/**
 * Every move so far against one saved revision, never just the latest drag.
 *
 * `sequence` rises with each drop, so the client can ignore an answer that
 * arrives after a newer one.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "LayoutCheckRequest".
 */
export interface LayoutCheckRequest {
  base_revision: number;
  moves: NodeMove[];
  sequence: number;
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "LayoutCheckResult".
 */
export interface LayoutCheckResult {
  blocked: Blocked[];
  findings: Finding[];
  graph_hash: string;
  sequence: number;
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "LidarMesh".
 */
export interface LidarMesh {
  floorY?: number | null;
  /**
   * @minItems 1
   * @maxItems 4096
   */
  parts: [LidarMeshPart, ...LidarMeshPart[]];
  peopleFilteringEnabled?: boolean | null;
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "LidarMeshPart".
 */
export interface LidarMeshPart {
  id: string;
  /**
   * @minItems 16
   * @maxItems 16
   */
  transform: [
    number,
    number,
    number,
    number,
    number,
    number,
    number,
    number,
    number,
    number,
    number,
    number,
    number,
    number,
    number,
    number,
    ...number[]
  ];
  /**
   * @minItems 3
   * @maxItems 6000000
   */
  triangles: [number, number, number, ...number[]];
  /**
   * @minItems 3
   * @maxItems 3000000
   */
  vertices: [number, number, number, ...number[]];
}
/**
 * Sent before the first pass runs.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "LoopStarted".
 */
export interface LoopStarted {
  base_revision: number;
  decided_by: string;
  kind: "started";
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "LoopPassFinished".
 */
export interface LoopPassFinished {
  kind: "pass";
  loop_pass: LoopPass;
}
/**
 * One trip round the loop: what the router chose and what came of it.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "LoopPass".
 */
export interface LoopPass {
  action: ("FIX" | "RESCAN_AREA" | "ASK_OWNER" | "ESCALATE" | "DONE") | null;
  inches_short_after: number | null;
  inches_short_before: number | null;
  kept: boolean | null;
  message: string;
  moves: NodeMove[];
  number: number;
  problems: number;
  question: string | null;
  questions: number;
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "LoopFinished".
 */
export interface LoopFinished {
  kind: "finished";
  result: LoopResult;
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "LoopResult".
 */
export interface LoopResult {
  base_revision: number;
  decided_by: string;
  moves: NodeMove[];
  passes: LoopPass[];
}
/**
 * The loop broke partway through; nothing was saved.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "LoopFailed".
 */
export interface LoopFailed {
  error: string;
  kind: "failed";
}
/**
 * Run Lane C's loop on this layout until it clears what it can or stops.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "LoopRequest".
 */
export interface LoopRequest {
  base_revision: number;
}
/**
 * A person points at the pixels of a target the pipeline did not find.
 *
 * With a `node_id` the mark attaches to that measured object. Without one it
 * is stored unlocalized. Either way the evidence is the actual crop, and the
 * server records who marked it and when.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "ManualMarkRequest".
 */
export interface ManualMarkRequest {
  frame_id: string;
  node_id: string | null;
  note: string | null;
  review_status: "candidate" | "confirmed_by_user";
  /**
   * @minItems 4
   * @maxItems 4
   */
  sensor_box: [number, number, number, number, ...number[]];
  target_class:
    | "outlet"
    | "television"
    | "service_counter"
    | "restroom_entrance"
    | "sofa"
    | "table"
    | "whiteboard"
    | "monitor"
    | "other";
}
/**
 * Row-major 4x4 transform.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "Mat4".
 */
export interface Mat4 {
  /**
   * @minItems 16
   * @maxItems 16
   */
  m: [
    number,
    number,
    number,
    number,
    number,
    number,
    number,
    number,
    number,
    number,
    number,
    number,
    number,
    number,
    number,
    number,
    ...number[]
  ];
}
/**
 * Nodes the primitive selected, such as everything standing on a desk.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "NodeSet".
 */
export interface NodeSet {
  node_ids: string[];
  type: "nodes";
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "NodeTextureCoverage".
 */
export interface NodeTextureCoverage {
  node_id: string;
  textured_fraction: number;
}
/**
 * A photographed crop evidencing the object.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "ObservationCrop".
 */
export interface ObservationCrop {
  confidence: number;
  frame_id: string;
  image_url: string | null;
  marked_at: string | null;
  marked_by: string | null;
  note: string | null;
  provenance: "automatic" | "manual";
  /**
   * @minItems 4
   * @maxItems 4
   */
  sensor_box: [number, number, number, number, ...number[]];
}
/**
 * Written by the phone after its photos upload. A build waits until every listed frame is stored.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "PhotoManifest".
 */
export interface PhotoManifest {
  /**
   * @minItems 1
   * @maxItems 4000
   */
  frames: [PhotoManifestFrame, ...PhotoManifestFrame[]];
  manifest_version: 1;
  poses_sha256: string;
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "PhotoManifestFrame".
 */
export interface PhotoManifestFrame {
  bytes: number;
  frame_id: string;
  sha256: string;
}
/**
 * A named point a row's evidence is about, for a map marker or a crop link.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "PointRef".
 */
export interface PointRef {
  name: string;
  position: Vec3;
}
/**
 * One keyframe in poses.json. Version 1 records lack the image metadata.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "PoseRecord".
 */
export interface PoseRecord {
  calibration_height: number | null;
  calibration_width: number | null;
  frame_id: string | null;
  image: string;
  image_height: number | null;
  image_orientation: "sensor" | null;
  image_width: number | null;
  /**
   * @minItems 9
   * @maxItems 9
   */
  intrinsics: [number, number, number, number, number, number, number, number, number, ...number[]];
  metadata_version: number;
  orientation: string;
  timestamp: number;
  /**
   * @minItems 16
   * @maxItems 16
   */
  transform: [
    number,
    number,
    number,
    number,
    number,
    number,
    number,
    number,
    number,
    number,
    number,
    number,
    number,
    number,
    number,
    number,
    ...number[]
  ];
}
/**
 * One primitive's answer, with everything needed to show or cite it.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "PrimitiveResult".
 */
export interface PrimitiveResult {
  evidence: Evidence;
  note: string | null;
  payload: (Quantity | NodeSet | TextSet | Truth) | null;
  primitive: string;
  quality: "measured" | "needs_another_look" | "not_measurable";
}
/**
 * One measured number, in the unit the standard is written in.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "Quantity".
 */
export interface Quantity {
  type: "quantity";
  unit: string;
  value: number;
}
/**
 * Words read off surfaces in the room.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "TextSet".
 */
export interface TextSet {
  texts: string[];
  type: "texts";
}
/**
 * A yes or no the geometry settled, such as whether a body fits.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "Truth".
 */
export interface Truth {
  type: "truth";
  value: boolean;
}
/**
 * A primitive as a model is shown it: a name, what it does, its arguments.
 *
 * This is the whole vocabulary a planner gets. A plan naming anything outside
 * it is refused before it runs.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "PrimitiveSpec".
 */
export interface PrimitiveSpec {
  arguments: {
    [k: string]: unknown;
  };
  name: string;
  returns: "quantity" | "nodes" | "texts" | "truth";
  summary: string;
}
/**
 * Ask the fix agent for a layout that clears these findings.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "ProposalRequest".
 */
export interface ProposalRequest {
  base_revision: number;
  finding_ids: string[];
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "ProposalResult".
 */
export interface ProposalResult {
  base_revision: number;
  message: string;
  proposal: Proposal | null;
  question: string | null;
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "RebuildRequest".
 */
export interface RebuildRequest {
  base_revision: number;
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "ReplayChapter".
 */
export interface ReplayChapter {
  evaluation: number;
  outcome: "route_blocked" | "out_of_reach" | "route_and_reach_fit";
  seconds: number;
  task: string;
}
/**
 * Everything the printed report shows, in one response.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "Report".
 */
export interface Report {
  assessment: Assessment | null;
  preview: boolean;
  rules: ReviewedRule[];
  scan: Scan;
  scenario: Scenario | null;
  scene: SceneGraph | null;
}
/**
 * A rule that ran, and the person who read its section and confirmed the number.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "ReviewedRule".
 */
export interface ReviewedRule {
  check: Check;
  second_check_by: string | null;
  verified_at: string;
  verified_by: string;
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "Scan".
 */
export interface Scan {
  artifacts: Artifact[];
  content_hash: string | null;
  coverage: SurfaceCoverage[];
  created_at: string;
  device_model: string;
  duration_seconds: number;
  id: string;
  name: string;
  state: "uploading" | "measuring" | "checking" | "ready" | "failed";
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "SurfaceCoverage".
 */
export interface SurfaceCoverage {
  node_id: string;
  observed_fraction: number;
  viewpoint_count: number;
}
/**
 * The routine we screen. Legs run between consecutive stops.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "Scenario".
 */
export interface Scenario {
  name: string;
  /**
   * @minItems 2
   */
  stops: [Stop, Stop, ...Stop[]];
}
/**
 * A destination on a customer's route.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "Stop".
 */
export interface Stop {
  anchor_node_id: string | null;
  name: string;
  position: Vec3;
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "SceneGraph".
 */
export interface SceneGraph {
  base_hash: string | null;
  capture_to_room?: Mat4 | null;
  nodes: SceneNode[];
  revision: number;
  scan_id: string;
  unlocalized_observations?: UnlocalizedObservation[];
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "SceneNode".
 */
export interface SceneNode {
  appearance?: DisplayAppearance | null;
  attachment?: SurfaceAttachment | null;
  dimensions: Vec3;
  id: string;
  kind: string;
  label: string;
  labeled_by: string;
  movable: boolean;
  parent_id: string | null;
  quality: "measured" | "needs_another_look" | "confirmed";
  raw_category: string;
  reconstruction?: DisplayReconstruction | null;
  relation?: string | null;
  texts?: SurfaceText[];
  transform: Mat4;
}
/**
 * Explicit surface-attached mounting metadata for thin/wall-mounted objects.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "SurfaceAttachment".
 */
export interface SurfaceAttachment {
  identity_confidence: number;
  local_anchor: Vec3 | null;
  localization_quality: "verified_support" | "inferred_plane" | "unanchored" | "needs_verification";
  normal: Vec3 | null;
  observations: ObservationCrop[];
  observed_region: Vec3[];
  review_status: "detected" | "candidate" | "confirmed_by_user" | "rejected_by_user";
  sockets: SocketTarget[];
  support_node_id: string | null;
  support_type: "lidar_surface" | "roomplan_plane" | "unanchored";
  uncertainty_reasons: string[];
}
/**
 * An individual operable socket opening on an outlet faceplate or power strip.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "SocketTarget".
 */
export interface SocketTarget {
  center: Vec3;
  confidence: number;
  id: string;
  status: "observed" | "inferred" | "unknown";
}
/**
 * Words read off a surface, and the frames they were read from.
 *
 * A whiteboard, a sign, a label on a box. The text is evidence about what the
 * room says, never about what it measures.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "SurfaceText".
 */
export interface SurfaceText {
  confidence: number;
  /**
   * @minItems 1
   * @maxItems 8
   */
  evidence_frame_ids: [string, ...string[]];
  face: ("top" | "front" | "back" | "left" | "right" | "bottom") | null;
  text: string;
}
/**
 * A real thing photographed where no reliable measured surface places it.
 *
 * It stays a first-class observation with its source pixels; it is not a
 * SceneNode, because giving it a position would invent geometry. Only a
 * person can create one, and every consumer must show it as unlocalized.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "UnlocalizedObservation".
 */
export interface UnlocalizedObservation {
  frame_id: string;
  id: string;
  marked_at: string | null;
  marked_by: string | null;
  note: string | null;
  provenance: "automatic" | "manual";
  review_status: "candidate" | "confirmed_by_user" | "rejected_by_user";
  /**
   * @minItems 4
   * @maxItems 4
   */
  sensor_box: [number, number, number, number, ...number[]];
  target_class:
    | "outlet"
    | "television"
    | "service_counter"
    | "restroom_entrance"
    | "sofa"
    | "table"
    | "whiteboard"
    | "monitor"
    | "other";
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "RulePack".
 */
export interface RulePack {
  checks: Check[];
  version: string;
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "SaveLayoutRequest".
 */
export interface SaveLayoutRequest {
  base_revision: number;
  moves: NodeMove[];
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "ScanList".
 */
export interface ScanList {
  scans: Scan[];
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "SimulationFeedback".
 */
export interface SimulationFeedback {
  blocking_node_ids: string[];
  clearance_failure_trials: number;
  floor_plan_collision_trials: number;
  mesh_collision_trials: number;
  needs_measurement_trials: number;
  passed_trials: number;
  profile_title: string;
  trials: number;
  unreachable_interaction_trials: number;
  workflow_title: string;
}
/**
 * A saved functional campaign, separate from live legal screening jobs.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "SimulationReplay".
 */
export interface SimulationReplay {
  /**
   * @minItems 1
   */
  chapters: [ReplayChapter, ...ReplayChapter[]];
  connectivity_builds: number;
  duration_seconds: number;
  evaluations: number;
  graph_hash: string;
  limitations: string[];
  report_sha256: string;
  revision: number;
  scan_id: string;
  selection: string;
  task_source: string;
  typesafe_calls: number;
  unique_layouts: number;
  video_sha256: string;
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "SimulationRequest".
 */
export interface SimulationRequest {
  astra_rounds: number;
  base_revision: number;
  exhaustive_evaluations: number;
  max_workers: number;
  refine_with_astra: boolean;
  router: "local" | "typesafe";
  samples: number;
  typesafe_call_limit: number;
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "SimulationResult".
 */
export interface SimulationResult {
  action_counts: {
    [k: string]: number;
  };
  ada_rule_violations: number;
  adaptive_rounds: AdaptiveRoundResult[];
  astra_calls: number;
  completed_runs: number;
  converged: boolean;
  exhaustive_evaluations: number;
  exhaustive_outcomes: {
    [k: string]: number;
  };
  feedback: SimulationFeedback[];
  limitations: string[];
  loop_cycles: number;
  mesh_checked: boolean;
  physics: EnvironmentPhysicsResult | null;
  preview: boolean;
  recommended_graph: SceneGraph | null;
  redesign_accepted: boolean;
  redesign_model: string | null;
  redesign_reasons: string[];
  rejected_runs: number;
  rejection_counts: {
    [k: string]: number;
  };
  rules_checked: number;
  rules_total: number;
  total_runs: number;
  typesafe_calls: number;
  unique_layouts: number;
  violating_trials: number;
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "SimulationStatus".
 */
export interface SimulationStatus {
  base_revision: number;
  candidate_graph: SceneGraph | null;
  completed: number;
  cycle: number;
  error: string | null;
  exhaustive_evaluations: number;
  result: SimulationResult | null;
  router: "local" | "typesafe";
  samples: number;
  state: "queued" | "running" | "done" | "failed";
  typesafe_call_limit: number;
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "TextureBuild".
 */
export interface TextureBuild {
  bake_graph: SceneGraph;
  build_id: string;
  coverage: TextureCoverage;
  coverage_mask_urls: string[];
  frames_used: number;
  glb_url: string;
  scan_glb_url: string | null;
  seconds: number;
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "TextureCoverage".
 */
export interface TextureCoverage {
  needs_another_view: string[];
  nodes: NodeTextureCoverage[];
  textured_fraction: number;
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "TextureRequest".
 */
export interface TextureRequest {
  revision?: number | null;
}
/**
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "TextureStatus".
 */
export interface TextureStatus {
  build: TextureBuild | null;
  can_retry: boolean;
  error: string | null;
  exact: boolean;
  revision: number;
  scan_id: string;
  stale_node_ids: string[];
  state: "needs_photos" | "waiting_for_photos" | "not_started" | "queued" | "running" | "complete" | "failed";
}
/**
 * The bottleneck of a route leg, and where it is.
 *
 * This interface was referenced by `StandardPhysicsContracts`'s JSON-Schema
 * via the `definition` "WidthResult".
 */
export interface WidthResult {
  blocking_node_ids: string[];
  inches: number;
  needs_measurement: boolean;
  path: Vec3[];
  pinch_point: Vec3;
  reachable: boolean;
}
