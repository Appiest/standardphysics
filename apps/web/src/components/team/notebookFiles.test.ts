import path from "node:path";
import { describe, expect, it } from "vitest";
import { cacheControl, contentType, notebookFile } from "./notebookFiles";

const ROOT = path.join("/srv", "notebooks", "finetune-story");

describe("notebookFile", () => {
  it("finds a file inside the export", () => {
    expect(notebookFile(ROOT, ["assets", "index-abc.js"])).toBe(path.join(ROOT, "assets", "index-abc.js"));
  });

  it("refuses a path that climbs out of the export", () => {
    expect(notebookFile(ROOT, ["..", "..", "etc", "passwd"])).toBeNull();
    expect(notebookFile(ROOT, ["assets", "..", "..", "secrets.json"])).toBeNull();
  });

  it("refuses the export folder itself", () => {
    expect(notebookFile(ROOT, [])).toBeNull();
  });

  it("refuses a sibling folder that shares the export's name as a prefix", () => {
    expect(notebookFile(ROOT, ["..", "finetune-story-private", "a.json"])).toBeNull();
  });
});

describe("contentType", () => {
  it("serves WebAssembly with the type browsers require to stream it", () => {
    expect(contentType("pyodide.asm.wasm")).toBe("application/wasm");
  });

  it("falls back to a download type for anything unknown", () => {
    expect(contentType("model.bin")).toBe("application/octet-stream");
  });
});

describe("cacheControl", () => {
  it("never caches the page or its data", () => {
    expect(cacheControl(["index.html"])).toBe("private, no-store");
    expect(cacheControl(["public", "finetune_ledger.json"])).toBe("private, no-store");
  });

  it("keeps hashed assets for a day", () => {
    expect(cacheControl(["assets", "index-abc.js"])).toContain("immutable");
  });
});
