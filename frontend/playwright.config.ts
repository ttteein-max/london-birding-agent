import { defineConfig, devices } from "@playwright/test";

const tempRoot = `/tmp/london-biodiversity-phase5-e2e-${process.pid}`;
const apiPort = Number(process.env.PHASE5_E2E_API_PORT ?? "18005");
const frontendPort = Number(process.env.PHASE5_E2E_FRONTEND_PORT ?? "15175");
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
        BIODIVERSITY_ROUTE_RUNTIME: `${tempRoot}-routes`,
        BIODIVERSITY_DATA_MODE: "fixture",
        BIODIVERSITY_MODEL_MODE: "scripted",
        BIODIVERSITY_BASEMAP_STYLE_URL: "https://tiles.openfreemap.org/styles/liberty",
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
