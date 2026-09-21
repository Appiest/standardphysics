/** Yields each non-empty line of a newline-delimited stream as soon as its newline arrives. */
export async function* readLines(body: ReadableStream<Uint8Array>): AsyncGenerator<string> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let pending = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    pending += decoder.decode(value, { stream: true });
    const lines = pending.split("\n");
    pending = lines.pop() ?? "";
    yield* lines.filter((line) => line.trim() !== "");
  }
  pending += decoder.decode();
  if (pending.trim() !== "") yield pending;
}
