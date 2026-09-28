import type { LayoutCheckResult, NodeMove } from "@/types/contracts";
import type { MoveSet } from "./moves";

export type RunCheck = (moves: NodeMove[], sequence: number) => Promise<LayoutCheckResult>;

export type Checked = { key: string; moves: MoveSet; result: LayoutCheckResult; milliseconds: number };

type Listeners = {
  onResult: (checked: Checked) => void;
  onError: (moves: MoveSet) => void;
  onBusy: (busy: boolean) => void;
};

/** Millimetres and tenths of a degree: finer than a drag can mean, coarse enough that float noise shares a key. */
export function layoutKey(moves: MoveSet): string {
  return Object.values(moves)
    .sort((a, b) => a.node_id.localeCompare(b.node_id))
    .map((move) => `${move.node_id}:${move.delta_translation.x.toFixed(3)},${move.delta_translation.y.toFixed(3)},${move.delta_rotation_z_degrees.toFixed(1)}`)
    .join("|");
}

/**
 * Checks layouts one at a time against the server, always the newest one asked for.
 *
 * The API assesses under a lock, so firing a request per pointer move would
 * queue work nobody will read. While one check is out, later asks collapse into
 * a single follow-up for whatever layout is newest when it returns. Answers are
 * cached by layout, so undo, snap-back and returning to a spot need no request.
 */
export class LayoutChecker {
  private readonly cache = new Map<string, LayoutCheckResult>();
  private inFlight = false;
  private queued: MoveSet | null = null;
  private sequence = 0;
  private generation = 0;

  constructor(private readonly run: RunCheck, private readonly listeners: Listeners) {}

  cached(moves: MoveSet): LayoutCheckResult | undefined {
    return this.cache.get(layoutKey(moves));
  }

  request(moves: MoveSet): void {
    const hit = this.cached(moves);
    if (hit) {
      this.queued = null;
      this.listeners.onResult({ key: layoutKey(moves), moves, result: hit, milliseconds: 0 });
      return;
    }
    if (this.inFlight) {
      this.queued = moves;
      return;
    }
    void this.send(moves, this.generation);
  }

  /** Forgets anything queued and ignores the answer still on its way. The cache stays. */
  cancel(): void {
    this.generation += 1;
    this.queued = null;
    if (this.inFlight) this.listeners.onBusy(false);
  }

  private async send(moves: MoveSet, generation: number): Promise<void> {
    this.inFlight = true;
    this.listeners.onBusy(true);
    const started = performance.now();
    try {
      const result = await this.run(Object.values(moves), ++this.sequence);
      const key = layoutKey(moves);
      this.cache.set(key, result);
      if (generation === this.generation) this.listeners.onResult({ key, moves, result, milliseconds: performance.now() - started });
    } catch {
      if (generation === this.generation) this.listeners.onError(moves);
    } finally {
      this.inFlight = false;
      this.sendQueued(generation);
    }
  }

  private sendQueued(generation: number): void {
    const next = generation === this.generation ? this.queued : null;
    this.queued = null;
    if (next) {
      this.request(next);
      return;
    }
    this.listeners.onBusy(false);
  }
}
