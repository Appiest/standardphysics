import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@fixtures": fileURLToPath(new URL("../../packages/fixtures/standardphysics_fixtures/data", import.meta.url)),
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/**/*.test.{ts,tsx}", "src/types/contracts.ts"],
      reporter: [
        "text-summary",
        ["text-summary", { file: "summary.txt" }],
        ["text", { file: "files.txt" }],
      ],
      thresholds: {
        "src/lib/**": { statements: 75, branches: 70, functions: 70, lines: 75 },
      },
    },
  },
});
