import { existsSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { defineConfig, devices } from "@playwright/test";
import { DEMO_EMAIL, DEMO_PASSWORD } from "./e2e/demo-account";
import { DEFAULT_API_PORT } from "./src/lib/api-origin";

/**
 * The owner and share-link flows, end to end, against the real API and a production build.
 *
 * Run `npm run build` first: `next start` serves that build, and the build
 * bakes in the API origin from SP_API_PORT, so both must see the same port.
 * Each run gets an empty data directory, so the only shop is the seeded one.
 */

const repositoryRoot = path.join(__dirname, "..", "..");
const localPython = path.join(repositoryRoot, ".venv", "bin", "python");
const python = process.env.PYTHON ?? (existsSync(localPython) ? localPython : "python3");
const apiPort = process.env.SP_API_PORT ?? String(DEFAULT_API_PORT);
const webPort = process.env.E2E_WEB_PORT ?? "3000";

export default defineConfig({
  testDir: "e2e",
  timeout: 90_000,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: `"${python}" -m standardphysics_api`,
      cwd: repositoryRoot,
      url: `http://127.0.0.1:${apiPort}/health`,
      reuseExistingServer: false,
      timeout: 60_000,
      stdout: "pipe",
      env: {
        SP_API_HOST: "127.0.0.1",
        SP_API_PORT: apiPort,
        SP_DATA_DIR: mkdtempSync(path.join(tmpdir(), "sp-e2e-")),
        SP_SEED_SAMPLE_SHOP: "1",
        SP_SEED_OWNER_EMAIL: DEMO_EMAIL,
        SP_SEED_OWNER_PASSWORD: DEMO_PASSWORD,
      },
    },
    {
      command: `npm run start -- --hostname 127.0.0.1 --port ${webPort}`,
      url: `http://127.0.0.1:${webPort}/sign-in`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: { SP_API_PORT: apiPort },
    },
  ],
});
