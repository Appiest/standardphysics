import { describe, expect, it } from "vitest";
import type { ModelLoopEvent } from "@/types/contracts";
import { advanceModelLoop, CONNECTION_LOST, type ModelLoopAction, NOT_STARTED } from "./model-loop-progress";

function event(fields: Partial<ModelLoopEvent>): ModelLoopEvent {
  return { kind: "turn", turn: null, picked: [], why: "", fixable_left: null, working_on: [], turns_at_most: null, construction: [], built_ins: [], moves: [], explanation: null, message: "", ...fields };
}

const replay = (actions: ModelLoopAction[]) => actions.reduce(advanceModelLoop, NOT_STARTED);

describe("model loop progress", () => {
  it("keeps the starting count, each turn as it lands, then the finished result", () => {
    const turn = event({ turn: 1, picked: ["slide the table"], fixable_left: 1 });
    const running = replay([{ kind: "start" }, event({ kind: "started", fixable_left: 3 }), turn]);
    expect(running).toMatchObject({ phase: "running", startedWith: 3, turns: [turn] });
    const finished = event({ kind: "finished", fixable_left: 0 });
    expect(advanceModelLoop(running, finished)).toMatchObject({ phase: "finished", finished });
  });

  it("tracks what the next turn works on and how many turns the run can take", () => {
    const started = event({ kind: "started", fixable_left: 2, turns_at_most: 5, working_on: ["Path to the counter", "Turning space"] });
    const turn = event({ turn: 1, picked: ["slide the chair"], fixable_left: 1, working_on: ["Turning space"] });
    expect(replay([{ kind: "start" }, started])).toMatchObject({ turnsAtMost: 5, workingOn: ["Path to the counter", "Turning space"] });
    expect(replay([{ kind: "start" }, started, turn]).workingOn).toEqual(["Turning space"]);
  });

  it("shows the server's reason when the run fails", () => {
    expect(replay([{ kind: "start" }, event({ kind: "failed", message: "No model is set up to run the loop." })]))
      .toMatchObject({ phase: "failed", error: "No model is set up to run the loop." });
  });

  it("ignores lines after Stop, and treats an early close as a lost connection", () => {
    expect(replay([{ kind: "start" }, { kind: "stop" }, event({ turn: 1 })])).toMatchObject({ phase: "stopped", turns: [] });
    expect(replay([{ kind: "start" }, { kind: "closed" }])).toMatchObject({ phase: "failed", error: CONNECTION_LOST });
    expect(replay([{ kind: "start" }, event({ kind: "finished" }), { kind: "closed" }]).phase).toBe("finished");
  });
});
