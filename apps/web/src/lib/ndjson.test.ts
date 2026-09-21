import { describe, expect, it } from "vitest";
import { readLines } from "./ndjson";

function streamOf(chunks: Uint8Array[]) {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      chunks.forEach((chunk) => controller.enqueue(chunk));
      controller.close();
    },
  });
}

async function collect(chunks: Uint8Array[]) {
  const lines: string[] = [];
  for await (const line of readLines(streamOf(chunks))) lines.push(line);
  return lines;
}

describe("readLines", () => {
  it("joins a line split across chunks and skips blank lines", async () => {
    const encode = (text: string) => new TextEncoder().encode(text);
    expect(await collect([encode('{"a":1}\n{"b"'), encode(':2}\n\n'), encode('{"c":3}')])).toEqual(['{"a":1}', '{"b":2}', '{"c":3}']);
  });

  it("keeps a multi-byte character whole when a chunk ends inside it", async () => {
    const bytes = new TextEncoder().encode("5½ in\n");
    expect(await collect([bytes.slice(0, 2), bytes.slice(2)])).toEqual(["5½ in"]);
  });
});
