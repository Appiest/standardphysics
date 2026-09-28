import ledgerJson from "../../../../../notebooks/public/finetune_ledger.json";

/** The ledger scripts/finetune_ledger.py collects from compute-box, the same file notebooks/finetune_story.py draws. */
type Evaluation = { label: string; samples: number; cleared: number };

export type FinetuneRun = {
  key: string;
  title: string;
  models: string[];
  training: { sft_rows: number | null; rl_steps_done: number | null };
  spend: { train_tokens: number };
  evaluations: Evaluation[];
  sessions: { opened_at: string }[];
};

type Ledger = { adapters: { name: string }[]; runs: FinetuneRun[] };

const ledger = ledgerJson as unknown as Ledger;

export const finetuneRuns = [...ledger.runs].sort((a, b) => a.sessions[0].opened_at.localeCompare(b.sessions[0].opened_at));

export const finetuneTotals = {
  adapters: ledger.adapters.length,
  rlSteps: finetuneRuns.reduce((total, run) => total + (run.training.rl_steps_done ?? 0), 0),
  trainTokens: finetuneRuns.reduce((total, run) => total + run.spend.train_tokens, 0),
};

export type StageChange = { stage: string; points: number };

/** Each trained stage's change in the share of held-out answers that cleared every fixable problem, in points. */
export function clearedChanges(run: FinetuneRun): StageChange[] {
  return run.evaluations.slice(1).map((entry, index) => ({
    stage: entry.label.replace("After ", ""),
    points: (entry.cleared - run.evaluations[index].cleared) * 100,
  }));
}

/** Runs graded before any training, so every change has a starting point on the same held-out rooms. */
export const benchmarkedRuns = finetuneRuns.filter((run) => run.evaluations[0]?.label === "Base");

export function promotedRl(run: FinetuneRun) {
  return run.models.some((model) => model.endsWith("-rl"));
}
