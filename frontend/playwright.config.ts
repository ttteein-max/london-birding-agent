import { defineConfig, devices } from "@playwright/test";

const tempRoot = `/tmp/london-biodiversity-phase4-e2e-${process.pid}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 20_000 },
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: "python -m uvicorn app.biodiversity.api.main:app --host 127.0.0.1 --port 8000",
      cwd: "..",
      env: {
        BIODIVERSITY_CHECKPOINT_DB: `${tempRoot}-checkpoints.sqlite`,
        BIODIVERSITY_RUN_CATALOG_DB: `${tempRoot}-catalog.sqlite`,
        BIODIVERSITY_REPORT_ROOT: `${tempRoot}-reports`,
        BIODIVERSITY_DATA_MODE: "fixture",
        BIODIVERSITY_MODEL_MODE: "scripted",
      },
      url: "http://127.0.0.1:8000/api/v1/health",
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: "npm run dev -- --port 5173",
      cwd: ".",
      url: "http://127.0.0.1:5173",
      reuseExistingServer: false,
      timeout: 30_000,
    },
  ],
});
