// Renders every reel to out/<id>.mp4 with its audio levelled for social (-14 LUFS, -1.5 dB true peak),
// plus a silent copy for posting under a trending sound.
import { execFileSync } from "node:child_process";
import { renameSync, rmSync } from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const out = (name) => path.join(root, "out", name);
const reels = process.argv.slice(2).length ? process.argv.slice(2) : ["Inches", "Pov", "OneLine"];

function render(id) {
  execFileSync("npx", ["remotion", "render", "src/index.ts", id, out(`${id}.raw.mp4`), "--codec=h264", "--crf=16", "--audio-bitrate=320k", "--gl=angle", `--concurrency=${process.env.CONCURRENCY ?? 3}`], { cwd: root, stdio: "inherit" });
}

export function level(id) {
  const ffmpeg = (args) => execFileSync("ffmpeg", ["-v", "error", "-y", ...args]);
  ffmpeg(["-i", out(`${id}.raw.mp4`), "-c:v", "copy", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-ar", "48000", "-c:a", "aac", "-b:a", "320k", out(`${id}.mp4`)]);
  ffmpeg(["-i", out(`${id}.mp4`), "-c:v", "copy", "-an", out(`${id}-silent.mp4`)]);
  rmSync(out(`${id}.raw.mp4`));
}

const levelOnly = process.env.LEVEL_ONLY === "1";
for (const id of reels) {
  if (levelOnly) renameSync(out(`${id}.mp4`), out(`${id}.raw.mp4`));
  else render(id);
  level(id);
}
