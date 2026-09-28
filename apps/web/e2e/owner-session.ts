import { expect, type Page } from "@playwright/test";
import { SAMPLE_SHOP_NAME } from "./demo-account";

export async function signIn(page: Page, email: string, password: string) {
  await page.goto("/sign-in");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL("/");
}

/** A brand new owner with no shops, so a spec can change their data without touching anyone else's. */
export async function signUpAsNewOwner(page: Page, shopName = "Another boba shop") {
  await page.goto("/sign-in?new");
  await page.getByLabel("What is your shop called?").fill(shopName);
  await page.getByLabel("Email").fill(`owner-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`);
  await page.getByLabel("Password").fill("a-fresh-owner-password");
  await page.getByRole("button", { name: "Create the account" }).click();
  await page.waitForURL("/");
}

export async function openSampleShop(page: Page): Promise<string> {
  await expect(page.getByRole("heading", { name: /^Your shops?$/ })).toBeVisible();
  await page.getByRole("link", { name: new RegExp(SAMPLE_SHOP_NAME) }).click();
  await page.waitForURL(/\/shops\/[0-9a-f-]+$/);
  await expect(page.getByText(SAMPLE_SHOP_NAME).first()).toBeVisible();
  return new URL(page.url()).pathname.split("/").pop()!;
}

/**
 * A shop the signed-in owner owns, made the way the iPhone app starts one.
 *
 * It never gets an upload, so it stays waiting for one, which is enough to
 * list it, open it and delete it.
 */
export async function createShopThroughApi(page: Page, name: string): Promise<string> {
  const response = await page.request.post("/api/scans", {
    data: { name, device_model: "Playwright", duration_seconds: 30, replaces: null, space_typology: null },
  });
  expect(response.status()).toBe(201);
  return ((await response.json()) as { id: string }).id;
}

const JOURNEY_WAIT_MS = 60_000;

/**
 * Walks the sample shop's owner steps to its results: no to each question,
 * skip each photo and measurement. Any step already taken is passed over, so
 * this works however far the shop has got.
 */
export async function reachSampleResults(page: Page) {
  const shareHeading = page.getByRole("heading", { name: "Share your report" });
  const nextAnswer = page.getByRole("button", { name: "No", exact: true }).or(page.getByRole("button", { name: "Skip for now" })).first();
  await expect(async () => {
    if (await shareHeading.isVisible()) return;
    if (await nextAnswer.isVisible()) await nextAnswer.click();
    await expect(shareHeading).toBeVisible({ timeout: 1_500 });
  }).toPass({ timeout: JOURNEY_WAIT_MS });
}
