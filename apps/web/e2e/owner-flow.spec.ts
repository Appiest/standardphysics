import { expect, test, type Page } from "@playwright/test";
import { DEMO_EMAIL, DEMO_PASSWORD, SAMPLE_SHOP_NAME } from "./demo-account";

/** The seeded assessment runs on the API's worker after startup, so the report can lag the shop by a few seconds. */
const ASSESSMENT_WAIT_MS = 60_000;

async function signIn(page: Page, email: string, password: string) {
  await page.goto("/sign-in");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL("/");
}

async function signUpAsNewOwner(page: Page) {
  await page.goto("/sign-in?new");
  await page.getByLabel("What is your shop called?").fill("Another boba shop");
  await page.getByLabel("Email").fill(`owner-${Date.now()}@example.com`);
  await page.getByLabel("Password").fill("a-fresh-owner-password");
  await page.getByRole("button", { name: "Create the account" }).click();
  await page.waitForURL("/");
}

async function openSampleShop(page: Page): Promise<string> {
  await expect(page.getByRole("heading", { name: "Your shop" })).toBeVisible();
  await page.getByRole("link", { name: new RegExp(SAMPLE_SHOP_NAME) }).click();
  await page.waitForURL(/\/shops\/[0-9a-f-]+$/);
  await expect(page.getByText(SAMPLE_SHOP_NAME).first()).toBeVisible();
  return new URL(page.url()).pathname.split("/").pop()!;
}

async function expectReportWithFindings(page: Page, scanId: string) {
  const problemsSection = page.locator("section", { has: page.getByRole("heading", { name: "What to fix" }) });
  await expect(async () => {
    await page.goto(`/scans/${scanId}/report`);
    await expect(problemsSection.getByRole("heading", { level: 3 }).first()).toBeVisible({ timeout: 1_000 });
  }).toPass({ timeout: ASSESSMENT_WAIT_MS });
  await expect(page.getByRole("heading", { level: 1, name: SAMPLE_SHOP_NAME })).toBeVisible();
}

async function expectNotFound(page: Page, path: string) {
  await page.goto(path);
  await expect(page.getByRole("heading", { name: "No such sheet" })).toBeVisible();
  await expect(page.getByText(SAMPLE_SHOP_NAME)).toHaveCount(0);
}

test("the demo owner opens the sample shop and its report, and nobody else can", async ({ page, browser }) => {
  await signIn(page, DEMO_EMAIL, DEMO_PASSWORD);
  const scanId = await openSampleShop(page);
  await expectReportWithFindings(page, scanId);

  const strangerContext = await browser.newContext();
  const stranger = await strangerContext.newPage();
  await signUpAsNewOwner(stranger);
  await expect(stranger.getByText("No drawings yet")).toBeVisible();
  await expectNotFound(stranger, `/shops/${scanId}`);
  await expectNotFound(stranger, `/scans/${scanId}/report`);
  await strangerContext.close();
});
