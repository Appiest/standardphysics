import { describe, expect, it } from "vitest";
import type { LoopPass, LoopResult } from "@/types/contracts";
import { advanceLoop, CONNECTION_LOST, NOT_STARTED, type LoopAction } from "./loop-progress";

const loopPass: LoopPass = {
  number: 1, action: "FIX", problems: 2, questions: 0, message: "", kept: true,
  inches_short_before: 16, inches_short_after: 11, moves: [], question: null,
};
const result: LoopResult = { base_revision: 0, decided_by: "typesafe", passes: [loopPass], moves: [] };

const replay = (actions: LoopAction[]) => actions.reduce(advanceLoop, NOT_STARTED);

describe("loop progress", () => {
  it("shows each pass as it arrives, then the finished result", () => {
    const running = replay([{ kind: "start" }, { kind: "started", base_revision: 0, decided_by: "typesafe" }, { kind: "pass", loop_pass: loopPass }]);
    expect(running).toMatchObject({ phase: "running", decidedBy: "typesafe", passes: [loopPass] });
    expect(advanceLoop(running, { kind: "finished", result })).toMatchObject({ phase: "finished", result });
  });

  it("ignores lines that arrive after the owner stops the loop", () => {
    const stopped = replay([{ kind: "start" }, { kind: "stop" }, { kind: "pass", loop_pass: loopPass }]);
    expect(stopped).toMatchObject({ phase: "stopped", passes: [] });
  });

  it("treats a stream that closes early as a lost connection, but not one that already finished", () => {
    expect(replay([{ kind: "start" }, { kind: "closed" }])).toMatchObject({ phase: "failed", error: CONNECTION_LOST });
    expect(replay([{ kind: "start" }, { kind: "finished", result }, { kind: "closed" }]).phase).toBe("finished");
  });

  it("starting again clears the last run", () => {
    expect(replay([{ kind: "start" }, { kind: "refused", error: "no route" }, { kind: "start" }])).toEqual({ ...NOT_STARTED, phase: "running" });
  });
});
