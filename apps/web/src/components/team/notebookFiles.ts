import path from "node:path";

/** Where `npm run notebook:export` writes the WebAssembly build of notebooks/finetune_story.py. Git ignores it. */
export const NOTEBOOK_ROOT = path.join(process.cwd(), ".notebooks", "finetune-story");

/** The URL the export is served under, behind the team check in its route handler. */
export const NOTEBOOK_URL = "/team/training/notebook/index.html";

const CONTENT_TYPES: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json",
  ".webmanifest": "application/manifest+json",
  ".wasm": "application/wasm",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".woff2": "font/woff2",
  ".woff": "font/woff",
  ".ttf": "font/ttf",
  ".txt": "text/plain; charset=utf-8",
  ".md": "text/plain; charset=utf-8",
  ".map": "application/json",
};

/** The file a request names inside the export, or null when the path would leave it. */
export function notebookFile(root: string, segments: string[]): string | null {
  const resolved = path.resolve(root, ...segments);
  return resolved.startsWith(root + path.sep) ? resolved : null;
}

export function contentType(file: string): string {
  return CONTENT_TYPES[path.extname(file).toLowerCase()] ?? "application/octet-stream";
}

/** The page and its data change with every export; the hashed assets under assets/ never do. */
export function cacheControl(segments: string[]): string {
  return segments[0] === "assets" ? "private, max-age=86400, immutable" : "private, no-store";
}
