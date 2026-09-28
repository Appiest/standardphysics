import { expect, test, type Page } from "@playwright/test";
import { DEMO_EMAIL, DEMO_PASSWORD, SAMPLE_SHOP_NAME } from "./demo-account";
import { openSampleShop, reachSampleResults, signIn } from "./owner-session";

async function makeShareLink(owner: Page): Promise<string> {
  await owner.getByRole("button", { name: "Share your report" }).click();
  const link = owner.getByText(/\/r\/[\w-]+$/);
  await expect(link).toBeVisible();
  return (await link.innerText()).trim();
}

async function stopEveryLink(owner: Page) {
  await owner.getByRole("button", { name: "Stop every link to this report" }).click();
  await owner.getByRole("button", { name: "Stop every link", exact: true }).click();
  await expect(owner.getByRole("status")).toHaveText("Every link to this report has stopped working.");
}

async function expectReadOnlyReport(reader: Page) {
  await expect(reader.getByRole("heading", { level: 1, name: SAMPLE_SHOP_NAME })).toBeVisible();
  await expect(reader.getByRole("heading", { name: "What to fix" })).toBeVisible();
  for (const ownerControl of ["Share your report", "Stop every link to this report", "Delete this shop", "Start fixing"]) {
    await expect(reader.getByRole("button", { name: ownerControl })).toHaveCount(0);
  }
}

test("a shared report opens read-only without an account and stops opening once the owner revokes it", async ({ page, browser }) => {
  await signIn(page, DEMO_EMAIL, DEMO_PASSWORD);
  await openSampleShop(page);
  await reachSampleResults(page);
  const shareUrl = await makeShareLink(page);

  const signedOutContext = await browser.newContext();
  const reader = await signedOutContext.newPage();
  await reader.goto(shareUrl);
  await expectReadOnlyReport(reader);

  await stopEveryLink(page);
  await reader.reload();
  await expect(reader.getByRole("heading", { level: 1, name: "This link has expired" })).toBeVisible();
  await expect(reader.getByText(SAMPLE_SHOP_NAME)).toHaveCount(0);
  await signedOutContext.close();
});
