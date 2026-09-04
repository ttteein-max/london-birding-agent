import { expect, test, type Page } from "@playwright/test";

async function startExample(page: Page, label: string): Promise<string> {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "London Biodiversity Expedition Planner" })).toBeVisible();
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
  await expect(page.locator(".plan-columns > div").first().locator("li")).not.toHaveCount(0);
  await expect(page.getByLabel("Map legend", { exact: true })).toContainText("Evidence-grounded candidate");
  await page.getByText(/Event audit · \d+ live events/).click();
  await expect(page.getByLabel("Ordered agent events").locator("li")).not.toHaveCount(0);
  await page.getByRole("button", { name: /Inspect state for/ }).last().click();
  await expect(page.getByRole("heading", { name: "Safe state inspector" })).toBeVisible();
  await expect(page.getByText("Exact checkpoint")).toBeVisible();
});

test("robin taxonomy HITL resumes on the same thread", async ({ page }) => {
  const threadId = await startExample(page, "Taxonomy HITL");
  await expect(page.getByRole("heading", { name: "Taxon Selection", exact: true })).toBeVisible();
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
  await expect(page.locator(".identity-list").first().locator("li")).toHaveCount(2);

  const historyResponse = await page.request.get(`/api/v1/runs/${threadId}/history`);
  expect(historyResponse.ok()).toBeTruthy();
  const history = await historyResponse.json();
  const original = history.branches.find((branch: { parent_branch_id: string | null }) => !branch.parent_branch_id);
  const forked = history.branches.find((branch: { parent_branch_id: string | null }) => branch.parent_branch_id);
  expect(original?.final_checkpoint_id).toBeTruthy();
  expect(forked?.final_checkpoint_id).toBeTruthy();

  await page.getByLabel("Checkpoint A", { exact: true }).selectOption(original.final_checkpoint_id);
  await page.getByLabel("Checkpoint B", { exact: true }).selectOption(forked.final_checkpoint_id);
  await page.getByRole("button", { name: "Compare plans" }).click();
  await expect(page.locator(".comparison-results article.changed")).not.toHaveCount(0);
  await expect(page.getByText("The original final plan remains immutable when replaying or forking.")).toBeVisible();
  await expect(page.getByText("Original final", { exact: true })).toBeVisible();
});
