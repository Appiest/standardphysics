import { describe, expect, it, vi } from "vitest";
import { isValidElement, type ComponentProps, type ReactElement, type ReactNode } from "react";
import sceneGraph from "@fixtures/shop.scene_graph.json";
import type { SceneGraph } from "@/types/contracts";
import type { RoomGroup } from "@/lib/room-groups";
import Viewer from "./Viewer";

/** Viewer's only hook is a memo, so calling it outside a renderer just computes the value. */
vi.mock("react", async (importActual) => {
  const actual = await importActual<typeof import("react")>();
  return { ...actual, useMemo: <T,>(compute: () => T) => compute() };
});

type ViewerProps = ComponentProps<typeof Viewer>;

function surfacesIn(node: ReactNode): ReactElement<Partial<ViewerProps>>[] {
  if (Array.isArray(node)) return node.flatMap(surfacesIn);
  if (!isValidElement<{ children?: ReactNode }>(node)) return [];
  if (typeof node.type === "function" && node.type.name === "ShopSurfaces") return [node as ReactElement<Partial<ViewerProps>>];
  return surfacesIn(node.props.children);
}

const scene = sceneGraph as unknown as SceneGraph;

const baseProps: ViewerProps = {
  scene,
  exported: scene,
  arrange: null,
  route: null,
  dragging: false,
  cutWalls: true,
  glbUrl: null,
  lidarUrl: null,
  scanGlbUrl: null,
  pose: { position: [6, 6, 6], target: [0, 0, 0], fov: 40 },
  selected: null,
  onSelectNode: () => {},
  onClearSelection: () => {},
  materialMode: "plain",
  staleNodeIds: [],
  coverage: [],
};

/**
 * The viewer hands the surfaces every prop it was given.
 *
 * Both the combine meshes and the active-room highlight were added, typechecked
 * and shipped dead, because the one place that renders them was handed a list
 * of props written out by name and neither was on it. Optional props make that
 * silent: nothing fails to compile, and the feature simply never appears.
 */
describe("the viewer forwards what it is given", () => {
  it("gives the surfaces the combined walks and the highlighted pieces it was handed", () => {
    const rooms: RoomGroup[] = [{ name: "stacks", node_ids: [], scan_glb_url: "/api/stacks.glb" }];
    const combinedRooms = { rooms, placements: {} };
    const highlightNodeIds = ["counter"];
    const props: ViewerProps = { ...baseProps, combinedRooms, highlightNodeIds, picking: true };

    const [surfaces] = surfacesIn(Viewer(props));

    expect(surfaces, "Viewer should render the shop surfaces").toBeDefined();
    expect(surfaces.props).toMatchObject({ combinedRooms, highlightNodeIds, picking: true, scene: props.scene });
  });
});
