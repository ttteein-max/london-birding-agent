import { defineConfig, devices } from "@playwright/test";

const tempRoot = `/tmp/london-biodiversity-phase4-e2e-${process.pid}`;
const apiPort = Number(process.env.PHASE4_E2E_API_PORT ?? "18004");
const frontendPort = Number(process.env.PHASE4_E2E_FRONTEND_PORT ?? "15173");
const apiOrigin = `http://127.0.0.1:${apiPort}`;
const frontendOrigin = `http://127.0.0.1:${frontendPort}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 20_000 },
  reporter: "list",
  use: {
    baseURL: frontendOrigin,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: `python -m uvicorn app.biodiversity.api.main:app --host 127.0.0.1 --port ${apiPort}`,
      cwd: "..",
      env: {
        BIODIVERSITY_CHECKPOINT_DB: `${tempRoot}-checkpoints.sqlite`,
        BIODIVERSITY_RUN_CATALOG_DB: `${tempRoot}-catalog.sqlite`,
        BIODIVERSITY_REPORT_ROOT: `${tempRoot}-reports`,
        BIODIVERSITY_DATA_MODE: "fixture",
        BIODIVERSITY_MODEL_MODE: "scripted",
        BIODIVERSITY_CORS_ORIGINS: frontendOrigin,
      },
      url: `${apiOrigin}/api/v1/health`,
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: `npm run dev -- --port ${frontendPort}`,
      cwd: ".",
      env: {
        VITE_API_BASE_URL: apiOrigin,
      },
      url: frontendOrigin,
      reuseExistingServer: false,
      timeout: 30_000,
    },
  ],
});
