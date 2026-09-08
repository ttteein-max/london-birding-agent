import { expect, test, type Page } from "@playwright/test";

const TEST_BASEMAP_STYLE = {
  version: 8,
  name: "Offline Playwright basemap fixture",
  sources: {},
  layers: [{ id: "paper", type: "background", paint: { "background-color": "#e8e6dc" } }],
};

test.beforeEach(async ({ page }) => {
  // Default browser QA is hermetic: the production style URL is exercised but
  // fulfilled locally, and every other public tile request is blocked.
  await page.route("https://tiles.openfreemap.org/**", async (route) => {
    if (new URL(route.request().url()).pathname === "/styles/liberty") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(TEST_BASEMAP_STYLE),
      });
      return;
    }
    await route.abort("blockedbyclient");
  });
});

async function startExample(page: Page, label: string): Promise<string> {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "London Birding Agent" })).toBeVisible();
  await page.waitForTimeout(250);
  await page.getByRole("button", { name: label }).click();
  const acceptedResponse = page.waitForResponse((response) =>
    response.url().includes("/api/v1/runs")
    && response.request().method() === "POST"
    && response.status() === 202,
  );
  await page.getByRole("button", { name: "Start expedition" }).click();
  const accepted = await (await acceptedResponse).json();
  await expect(page.locator(".run-list button.selected .run-title")).toHaveText(accepted.thread_id);
  return accepted.thread_id;
}

async function waitForCompletedPlan(page: Page, threadId: string): Promise<void> {
  await expect(page.locator(".run-list button.selected .run-title")).toHaveText(threadId);
  await expect(page.locator(".run-list button.selected")).toContainText("Completed");
  await expect(page.getByRole("button", { name: "Start expedition" })).toBeEnabled();
  await expect(page.locator(".plan-panel")).toBeVisible();
}

test("Common woodpigeon strong-evidence happy path", async ({ page }) => {
  const threadId = await startExample(page, "Strong evidence");
  await waitForCompletedPlan(page, threadId);
  await expect(page.getByRole("heading", { name: "Natural-language request" })).toBeVisible();
  await expect(page.locator(".selected-request-card blockquote")).toContainText(
    "Plan a two-hour expedition from SW11 4NJ",
  );
  await expect(page.getByRole("heading", { name: "Columba palumbus" })).toBeVisible();
  await expect(page.getByText("Evidence gate passed", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Expedition overview" })).toBeVisible();
  await expect(page.locator(".plan-overview")).toContainText("total outing");
  await expect(page.getByRole("heading", { name: "Validated journey itinerary" })).toBeVisible();
  await expect(page.locator(".route-selection")).toContainText("deterministic routing code");
  await expect(page.getByText(/fixture-openrouteservice · foot-walking/)).toBeVisible();
  await expect(page.getByText(/Selected entrance:/)).toBeVisible();
  await expect(page.locator(".plan-columns > div").first().locator("li")).not.toHaveCount(0);
  await expect(page.getByLabel("Map legend", { exact: true })).toContainText("Evidence-grounded candidate");
  await page.getByText(/Event audit · \d+ live events/).click();
  await expect(page.getByLabel("Ordered agent events").locator("li")).not.toHaveCount(0);
  await page.getByRole("button", { name: /Inspect state for/ }).last().click();
  await expect(page.getByRole("heading", { name: "Safe state inspector" })).toBeVisible();
  await expect(page.getByText("Exact checkpoint")).toBeVisible();
});

test("walking-limit trade-off resumes with typed input on the same execution", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Natural-language request").fill(
    "Plan a three-hour expedition from SW11 4NJ on 15 June 2026 to look for Common woodpigeon with a maximum walking distance of 3 km.",
  );
  const acceptedResponse = page.waitForResponse((response) =>
    response.url().includes("/api/v1/runs")
    && response.request().method() === "POST"
    && response.status() === 202,
  );
  await page.getByRole("button", { name: "Start expedition" }).click();
  const accepted = await (await acceptedResponse).json();
  await expect(page.getByRole("heading", { name: "Route Tradeoff" })).toBeVisible();
  await expect(page.locator(".run-list button.selected .run-title")).toHaveText(accepted.thread_id);
  const limit = page.getByLabel("Maximum full-excursion walking distance (km)");
  await limit.fill("10");
  await page.getByRole("button", { name: "Recalculate routes" }).click();
  await waitForCompletedPlan(page, accepted.thread_id);
  await expect(page.getByRole("heading", { name: "Validated journey itinerary" })).toBeVisible();
  await expect(page.getByLabel("Walking route constraints")).toContainText("Pass");
});

test("basemap failure keeps evidence overlays and an explicit browser status", async ({ page }) => {
  await page.unroute("https://tiles.openfreemap.org/**");
  await page.route("https://tiles.openfreemap.org/**", (route) => route.abort("blockedbyclient"));
  const threadId = await startExample(page, "Strong evidence");
  await waitForCompletedPlan(page, threadId);
  await expect(page.getByText("Basemap unavailable — evidence overlays remain available")).toBeVisible();
  await expect(page.getByLabel("Map legend", { exact: true })).toContainText("Evidence-grounded candidate");
  await expect(page.getByLabel("Mapped sites as text").getByRole("button")).not.toHaveCount(0);
});

test("mobile layout keeps route facts and text alternatives accessible", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const threadId = await startExample(page, "Strong evidence");
  await waitForCompletedPlan(page, threadId);
  await expect(page.getByRole("heading", { name: "Evidence map" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Validated journey itinerary" })).toBeVisible();
  await expect(page.getByLabel("Walking route constraints")).toContainText("Pass");
  await expect(page.getByRole("img", { name: /Walking route elevation profile/ })).toBeVisible();
  await expect(page.locator("footer")).toContainText(
    "Journeys end at audited public-site entrances",
  );
  expect(await page.evaluate(
    () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
  )).toBe(true);
});

test("robin taxonomy HITL resumes on the same thread", async ({ page }) => {
  const threadId = await startExample(page, "Taxonomy HITL");
  await expect(page.getByRole("heading", { name: "Taxon Selection", exact: true })).toBeVisible();
  await expect(page.getByText("Not evaluated · preview budget exhausted").first()).toBeVisible();
  await expect(page.getByText(/Not Evaluated Budget · 0 retained/)).toHaveCount(0);
  await page.locator(".candidate-choice-grid button").first().click();
  await expect(page.getByRole("heading", { name: "Actionable Tradeoff", exact: true })).toBeVisible();
  await expect(page.locator(".run-list button.selected .run-title")).toHaveText(threadId);
  await expect(page.locator(".status-pill")).toContainText("Waiting For Input");
});

test("Kensal Road resolves through a typed location decision", async ({ page }) => {
  const threadId = await startExample(page, "Named place HITL");
  await expect(page.getByRole("heading", { name: "Location Correction", exact: true })).toBeVisible();
  await expect(page.locator(".location-choice-grid button")).toHaveCount(3);
  await expect(page.getByText("© OpenStreetMap contributors", { exact: true })).toBeVisible();
  await page.locator(".location-choice-grid button").first().click();
  await waitForCompletedPlan(page, threadId);
  await expect(page.getByRole("heading", { name: "Columba palumbus" })).toBeVisible();
  await expect(page.locator(".run-list button.selected .run-title")).toHaveText(threadId);
});

test("Common swift low-evidence acceptance remains context only", async ({ page }) => {
  const threadId = await startExample(page, "Low evidence");
  await expect(page.getByRole("heading", { name: "Actionable Tradeoff", exact: true })).toBeVisible();
  await page.getByRole("button", { name: /Keep Constraints Accept Low Confidence/ }).click();
  await waitForCompletedPlan(page, threadId);
  await expect(page.getByText("Evidence gate held", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Evidence-grounded candidate sites" })).toBeVisible();
  await expect(page.locator(".plan-columns > div").first()).toContainText("None at this evidence gate.");
  await expect(page.locator(".map-message.map-low")).toContainText("strong evidence gate did not pass");
});

test("forked constraints compare without replacing the original final", async ({ page }) => {
  const threadId = await startExample(page, "Strong evidence");
  await waitForCompletedPlan(page, threadId);
  await expect(page.getByText("Original final", { exact: true })).toBeVisible();
  await expect(page.getByText("Current value: 5", { exact: true })).toBeVisible();

  const forkValue = page.getByLabel("New value");
  await forkValue.fill("5");
  await expect(page.getByRole("button", { name: "Create fork" })).toBeDisabled();
  await forkValue.fill("8");
  await page.getByRole("button", { name: "Create fork" }).click();
  await expect(page.getByRole("button", { name: "Start expedition" })).toBeEnabled();
  const branchItems = page.locator(".branch-tree > .branch-tree-item");
  await expect(branchItems).toHaveCount(2);
  await expect(branchItems.nth(0)).toContainText("Original branch");
  await expect(branchItems.nth(0)).toContainText("Execution 1");
  await expect(branchItems.nth(1)).toContainText("Fork branch 1");
  await expect(branchItems.nth(1)).toContainText("Execution 2");

  const historyResponse = await page.request.get(`/api/v1/runs/${threadId}/history`);
  expect(historyResponse.ok()).toBeTruthy();
  const history = await historyResponse.json();
  const original = history.branches.find((branch: { parent_branch_id: string | null }) => !branch.parent_branch_id);
  const forked = history.branches.find((branch: { parent_branch_id: string | null }) => branch.parent_branch_id);
  expect(original?.final_checkpoint_id).toBeTruthy();
  expect(forked?.final_checkpoint_id).toBeTruthy();

  await page.getByLabel("Checkpoint A (left)", { exact: true }).selectOption(original.final_checkpoint_id);
  await page.getByLabel("Checkpoint B (right)", { exact: true }).selectOption(forked.final_checkpoint_id);
  await page.getByRole("button", { name: "Compare plans" }).click();
  await expect(page.locator(".comparison-results article.changed")).not.toHaveCount(0);
  await expect(page.getByText("The original final plan remains immutable when replaying or forking.")).toBeVisible();
  await expect(page.getByText("Original final", { exact: true })).toBeVisible();
});
