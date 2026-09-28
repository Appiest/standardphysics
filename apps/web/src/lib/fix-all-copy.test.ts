import { describe, expect, it } from "vitest";
import type { ModelLoopEvent, NodeMove } from "@/types/contracts";
import { finishedDetail, finishedHeadline, isAllCleared, problemsLeft, turnMoves, turnTitle } from "./fix-all-copy";
import { NOT_STARTED } from "./model-loop-progress";

const move: NodeMove = { node_id: "a", delta_translation: { x: 0.3, y: 0, z: 0 }, delta_rotation_z_degrees: 0 };

function event(fields: Partial<ModelLoopEvent>): ModelLoopEvent {
  return { kind: "turn", turn: null, picked: [], why: "", fixable_left: null, moves: [], explanation: null, message: "", ...fields };
}

describe("fix all copy", () => {
  it("counts down from the starting problems, then from each turn", () => {
    expect(problemsLeft({ ...NOT_STARTED, startedWith: 3 })).toBe(3);
    expect(problemsLeft({ ...NOT_STARTED, startedWith: 3, turns: [event({ fixable_left: 1 })] })).toBe(1);
  });

  it("names a turn by the move the model picked", () => {
    expect(turnTitle(event({ picked: ["slide the table 12 in toward the window"] }))).toBe("Slide the table 12 in toward the window");
    expect(turnTitle(event({}))).toBe("Picked nothing it could use");
  });

  it("lists each move of a turn that moved several pieces on its own line", () => {
    expect(turnMoves(event({ picked: ["slide Table 3 in", "set Card reader down"] }))).toEqual(["Slide Table 3 in", "Set Card reader down"]);
    expect(turnMoves(event({}))).toEqual(["Picked nothing it could use"]);
  });

  it("celebrates only a run that cleared everything with a real move", () => {
    const cleared = event({ kind: "finished", fixable_left: 0, moves: [move] });
    expect(finishedHeadline(cleared, 3)).toBe("All 3 problems fixed");
    expect(isAllCleared(cleared, 3)).toBe(true);
    expect(isAllCleared(event({ kind: "finished", fixable_left: 0 }), 0)).toBe(false);
  });

  it("says how many were fixed and why it stopped when the run falls short", () => {
    const partial = event({ kind: "finished", fixable_left: 2, moves: [move], message: "The menu has no move left for what remains." });
    expect(finishedHeadline(partial, 3)).toBe("Fixed 1 of 3 problems");
    expect(finishedDetail(partial, false)).toBe("1 piece moves. The menu has no move left for what remains.");
    const none = event({ kind: "finished", fixable_left: 3, message: "The model chose nothing it could use." });
    expect(finishedHeadline(none, 3)).toBe("No furniture move fixed a problem");
    expect(finishedDetail(none, false)).toBe("The layout stays as it is. The model chose nothing it could use.");
  });
});
