import { describe, expect, it } from "vitest";
import type { ModelLoopEvent, NodeMove } from "@/types/contracts";
import { finishedDetail, finishedHeadline, isAllCleared, namedProblems, problemsLeft, turnClock, turnInProgress, turnLines, turnTitle } from "./fix-all-copy";
import { NOT_STARTED } from "./model-loop-progress";

const move: NodeMove = { node_id: "a", delta_translation: { x: 0.3, y: 0, z: 0 }, delta_rotation_z_degrees: 0 };

function event(fields: Partial<ModelLoopEvent>): ModelLoopEvent {
  return { kind: "turn", turn: null, picked: [], why: "", fixable_left: null, working_on: [], turns_at_most: null, construction: [], built_ins: [], moves: [], explanation: null, message: "", ...fields };
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
    expect(turnLines(event({ picked: ["slide Table 3 in", "set Card reader down"] })).map((line) => line.text)).toEqual(["Slide Table 3 in", "Set Card reader down"]);
    expect(turnLines(event({})).map((line) => line.text)).toEqual(["Picked nothing it could use"]);
    const built = turnLines(event({ picked: ["slide Table 3 in", "slide Service counter 12 in"], construction: ["slide Service counter 12 in"] }));
    expect(built.map((line) => line.construction)).toEqual([false, true]);
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

  it("words the turn in progress as a count and a clock", () => {
    expect(turnInProgress(2, 5)).toBe("Turn 2 of up to 5");
    expect(turnInProgress(1, null)).toBe("Turn 1");
    expect(turnClock(7)).toBe("0:07");
    expect(turnClock(72)).toBe("1:12");
  });

  it("names the first few open problems and counts the rest", () => {
    expect(namedProblems(["a", "b"])).toEqual({ named: ["a", "b"], more: 0 });
    expect(namedProblems(["a", "b", "c", "d", "e"])).toEqual({ named: ["a", "b", "c"], more: 2 });
  });

  it("says when a built-in moves, because a contractor has to do it", () => {
    const one = event({ kind: "finished", moves: [move], built_ins: ["a"], fixable_left: 0 });
    expect(finishedDetail(one, true)).toBe("1 built-in piece moves. A contractor has to move it.");
    const mixed = event({ kind: "finished", moves: [move, { ...move, node_id: "b" }, { ...move, node_id: "c" }], built_ins: ["c"], fixable_left: 0 });
    expect(finishedDetail(mixed, true)).toBe("3 pieces move. One is built in, so a contractor has to move it.");
  });
});
