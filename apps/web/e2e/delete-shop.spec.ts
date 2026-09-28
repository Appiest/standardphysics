import { expect, test } from "@playwright/test";
import { createShopThroughApi, signUpAsNewOwner } from "./owner-session";

test("an owner deletes a shop after confirming, and it leaves their list", async ({ page }) => {
  await signUpAsNewOwner(page);
  const shopName = `Shop to delete ${Date.now()}`;
  const scanId = await createShopThroughApi(page, shopName);

  await page.goto("/");
  await page.getByRole("link", { name: new RegExp(shopName) }).click();
  await page.waitForURL(`/shops/${scanId}`);

  await page.getByRole("button", { name: "Delete this shop" }).click();
  await page.getByRole("button", { name: "Keep it" }).click();
  await expect(page.getByRole("button", { name: "Delete this shop" })).toBeVisible();

  await page.getByRole("button", { name: "Delete this shop" }).click();
  await expect(page.getByText(`Delete ${shopName}?`)).toBeVisible();
  await page.getByRole("button", { name: "Delete it" }).click();

  await page.waitForURL("/");
  await expect(page.getByText("No drawings yet")).toBeVisible();
  await expect(page.getByText(shopName)).toHaveCount(0);
  await page.goto(`/shops/${scanId}`);
  await expect(page.getByRole("heading", { name: "No such sheet" })).toBeVisible();
});
