import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiTimeout, fetchApi } from "./api-fetch";

vi.mock("next/headers", () => ({ cookies: async () => ({ get: () => undefined }) }));

function neverAnswers(_url: string, init?: RequestInit): Promise<Response> {
  return new Promise((_resolve, reject) => {
    init?.signal?.addEventListener("abort", () => reject(init.signal?.reason));
  });
}

const connectionReset = () => Promise.reject(new TypeError("fetch failed", { cause: { code: "ECONNRESET" } }));

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchApi", () => {
  it("gives up on a stalled API with a timeout error", async () => {
    vi.stubGlobal("fetch", vi.fn(neverAnswers));

    await expect(fetchApi("http://api/scans", { deadlineMs: 20 })).rejects.toBeInstanceOf(ApiTimeout);
  });

  it("does not retry a read that timed out", async () => {
    const fetchMock = vi.fn(neverAnswers);
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchApi("http://api/scans", { deadlineMs: 20 })).rejects.toThrow(/did not answer/);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("tries a read again after a connection reset and returns the answer", async () => {
    const fetchMock = vi.fn().mockImplementationOnce(connectionReset).mockResolvedValueOnce(new Response("{}"));
    vi.stubGlobal("fetch", fetchMock);

    const response = await fetchApi("http://api/scans");

    expect(response.ok).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("stops retrying after a bounded number of attempts", async () => {
    const fetchMock = vi.fn(connectionReset);
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchApi("http://api/scans")).rejects.toBeInstanceOf(TypeError);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("never retries a POST", async () => {
    const fetchMock = vi.fn(connectionReset);
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchApi("http://api/scans", { method: "POST", body: "{}" })).rejects.toBeInstanceOf(TypeError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not retry a read the API answered with an error", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("", { status: 503 }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await fetchApi("http://api/scans");

    expect(response.status).toBe(503);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("server-side loaders", () => {
  it("send every read with a deadline", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ scans: [] })));
    vi.stubGlobal("fetch", fetchMock);
    const { listScans, headSceneGlb } = await import("./api");

    await listScans();
    await headSceneGlb("scan-1");

    for (const [, init] of fetchMock.mock.calls) expect(init.signal).toBeInstanceOf(AbortSignal);
  });
});
