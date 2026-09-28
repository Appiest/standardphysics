import ledgerJson from "../../../../../notebooks/public/finetune_ledger.json";

/** The ledger scripts/finetune_ledger.py collects from compute-box, the same file notebooks/finetune_story.py draws. */
type Evaluation = { label: string; samples: number; cleared: number; rules_pass: number };

type Cleared = { cleared: number; of: number };

export type FinetuneRun = {
  key: string;
  title: string;
  models: string[];
  training: { sft_rows: number | null; rl_steps_done: number | null };
  spend: { train_tokens: number };
  evaluations: Evaluation[];
  sessions: { opened_at: string }[];
  five_loop?: { real_heldout: Record<string, Cleared> };
};

type Ledger = { adapters: { name: string }[]; runs: FinetuneRun[] };

const ledger = ledgerJson as unknown as Ledger;

export const finetuneRuns = [...ledger.runs].sort((a, b) => a.sessions[0].opened_at.localeCompare(b.sessions[0].opened_at));

export const finetuneTotals = {
  adapters: ledger.adapters.length,
  rlSteps: finetuneRuns.reduce((total, run) => total + (run.training.rl_steps_done ?? 0), 0),
  trainTokens: finetuneRuns.reduce((total, run) => total + run.spend.train_tokens, 0),
  graded: finetuneRuns.reduce((total, run) => total + run.evaluations.reduce((sum, entry) => sum + entry.samples, 0), 0),
};

/** Runs graded before any training, so every change has a starting point on the same held-out rooms. */
export const benchmarkedRuns = finetuneRuns.filter((run) => run.evaluations[0]?.label === "Base");

export type RuleShift = { run: FinetuneRun; before: number; after: number };

/** Share of held-out answers that broke no hard rule, before training and after the run's last stage. */
export const ruleShifts: RuleShift[] = benchmarkedRuns.map((run) => ({
  run,
  before: run.evaluations[0].rules_pass,
  after: run.evaluations[run.evaluations.length - 1].rules_pass,
}));

export const rulesRose = ruleShifts.filter((shift) => shift.after > shift.before).length;
export const rulesFell = ruleShifts.filter((shift) => shift.after < shift.before).length;

const fiveLoop = finetuneRuns.find((run) => run.five_loop)?.five_loop?.real_heldout ?? {};

/** The 2026-09-26 night: whole five-try loops on the 65 real held-out rooms, with and without the room solver. */
export const loopResults = ["Base model alone", "Solver alone, no model", "Base model with the solver"]
  .filter((label) => label in fiveLoop)
  .map((label) => ({ label, ...fiveLoop[label] }));

export function promotedRl(run: FinetuneRun) {
  return run.models.some((model) => model.endsWith("-rl"));
}
