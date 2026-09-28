/**
 * Every server-side request to the API goes through `fetchApi`.
 *
 * A page render waits on these reads, so a stalled API would otherwise hold
 * the render open until the browser gave up. Each request gets a deadline,
 * and a read that never reached the API (reset, refused) is tried again a
 * couple of times. A request that did reach it is never repeated, and neither
 * is anything that writes.
 */

export const DEFAULT_DEADLINE_MS = 10_000;

/** Path suggestions run route planning over the whole floor before answering. */
export const SLOW_DEADLINE_MS = 30_000;

export const RETRY_DELAYS_MS = [100, 300];

const IDEMPOTENT_METHODS = new Set(["GET", "HEAD"]);

export class ApiTimeout extends Error {
  constructor(url: string, deadlineMs: number) {
    super(`The API did not answer ${url} within ${deadlineMs / 1000} seconds`);
    this.name = "ApiTimeout";
  }
}

export type ApiRequest = RequestInit & { deadlineMs?: number };

function isTimeout(error: unknown): boolean {
  return error instanceof DOMException && (error.name === "TimeoutError" || error.name === "AbortError");
}

/** Undici rejects with a TypeError when the socket never produced a response. */
function isConnectionError(error: unknown): boolean {
  return error instanceof TypeError;
}

function isIdempotent(init: RequestInit): boolean {
  return IDEMPOTENT_METHODS.has((init.method ?? "GET").toUpperCase());
}

const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function fetchOnce(url: string, init: RequestInit, deadlineMs: number): Promise<Response> {
  try {
    return await fetch(url, { ...init, signal: AbortSignal.timeout(deadlineMs) });
  } catch (error) {
    if (isTimeout(error)) throw new ApiTimeout(url, deadlineMs);
    throw error;
  }
}

export async function fetchApi(url: string, { deadlineMs = DEFAULT_DEADLINE_MS, ...init }: ApiRequest = {}): Promise<Response> {
  const retryDelays = isIdempotent(init) ? RETRY_DELAYS_MS : [];
  for (const delay of retryDelays) {
    try {
      return await fetchOnce(url, init, deadlineMs);
    } catch (error) {
      if (!isConnectionError(error)) throw error;
      await pause(delay);
    }
  }
  return fetchOnce(url, init, deadlineMs);
}
