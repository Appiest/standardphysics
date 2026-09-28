// Usage: node scripts/stills.mjs <CompositionId> <frame> [frame...]
// Renders the frames and tiles them into out/<id>-sheet.jpg for review.
import { bundle } from "@remotion/bundler";
import { renderStill, selectComposition } from "@remotion/renderer";
import { execFileSync } from "node:child_process";
import { copyFileSync, mkdirSync } from "node:fs";
import path from "node:path";
import { enableTailwind } from "@remotion/tailwind-v4";

const [id, ...frames] = process.argv.slice(2);
const root = path.resolve(import.meta.dirname, "..");
const serveUrl = await bundle({ entryPoint: path.join(root, "src/index.ts"), webpackOverride: (config) => enableTailwind(config) });
const composition = await selectComposition({ serveUrl, id, chromiumOptions: { gl: "angle" } });
const target = path.join(root, "out", "stills", id);
mkdirSync(target, { recursive: true });
const outputs = [];
for (const frame of frames.map(Number)) {
  const output = path.join(target, `${String(frame).padStart(4, "0")}.jpg`);
  await renderStill({ serveUrl, composition, frame, output, imageFormat: "jpeg", jpegQuality: 85, chromiumOptions: { gl: "angle" }, scale: 0.5 });
  outputs.push(output);
  process.stdout.write(`${frame} `);
}
const sheet = path.join(root, "out", `${id}-sheet.jpg`);
if (outputs.length === 1) {
  copyFileSync(outputs[0], sheet);
  process.exit(0);
}
const columns = Math.min(6, outputs.length);
const inputs = outputs.flatMap((file) => ["-i", file]);
execFileSync("ffmpeg", ["-v", "error", "-y", ...inputs, "-filter_complex", `xstack=inputs=${outputs.length}:layout=${layout(outputs.length, columns)}:fill=gray`, sheet]);
console.log("\nsheet written");

function layout(count, cols) {
  return Array.from({ length: count }, (_, index) => `${(index % cols) * 540}_${Math.floor(index / cols) * 960}`).join("|");
}
