import { expect, test } from "@playwright/test";
import { DEMO_EMAIL, DEMO_PASSWORD } from "./demo-account";
import { createShopThroughApi, openSampleShop, reachSampleResults, signIn, signUpAsNewOwner } from "./owner-session";

test("a share request the API fails shows the app's own message, and the next try works", async ({ page }) => {
  await signIn(page, DEMO_EMAIL, DEMO_PASSWORD);
  await openSampleShop(page);
  await reachSampleResults(page);

  await page.route("**/api/scans/*/shares", (route) => route.fulfill({ status: 503, json: { error: "unavailable" } }), { times: 1 });
  await page.getByRole("button", { name: "Share your report" }).click();
  await expect(page.getByRole("region", { name: "Share your report" }).getByRole("status")).toHaveText(
    "We couldn't make a link. Try again.",
  );
  await expect(page.getByRole("heading", { name: "Share your report" })).toBeVisible();

  await page.getByRole("button", { name: "Share your report" }).click();
  await expect(page.getByText(/\/r\/[\w-]+$/)).toBeVisible();
});

test("a delete that never reaches the API says so and keeps the shop", async ({ page }) => {
  await signUpAsNewOwner(page);
  const shopName = `Shop the network drops ${Date.now()}`;
  const scanId = await createShopThroughApi(page, shopName);
  await page.goto(`/shops/${scanId}`);

  await page.route(`**/api/scans/${scanId}`, (route) => route.abort("internetdisconnected"), { times: 1 });
  await page.getByRole("button", { name: "Delete this shop" }).click();
  await page.getByRole("button", { name: "Delete it" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Unable to delete this shop. Check your connection and try again." })).toBeVisible();
  await expect(page.getByRole("button", { name: "Delete it" })).toBeEnabled();

  await page.goto("/");
  await expect(page.getByRole("link", { name: new RegExp(shopName) })).toBeVisible();
});
