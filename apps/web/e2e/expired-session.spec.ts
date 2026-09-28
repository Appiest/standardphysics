import { expect, test, type Page } from "@playwright/test";
import { createShopThroughApi, signUpAsNewOwner } from "./owner-session";

const SESSION_COOKIE = "sp_session";

async function expectSignInScreen(page: Page) {
  await page.waitForURL("/sign-in");
  await expect(page.getByLabel("Email")).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
}

async function corruptSessionCookie(page: Page) {
  const context = page.context();
  const [session] = (await context.cookies()).filter((cookie) => cookie.name === SESSION_COOKIE);
  expect(session).toBeDefined();
  await context.addCookies([{ ...session, value: `${session.value}-no-longer-valid` }]);
}

test("a session the API no longer accepts sends the owner to sign in on their next click", async ({ page }) => {
  await signUpAsNewOwner(page);
  const shopName = `Shop behind a stale cookie ${Date.now()}`;
  await createShopThroughApi(page, shopName);
  await page.goto("/");

  await corruptSessionCookie(page);
  await page.getByRole("link", { name: new RegExp(shopName) }).click();
  await expectSignInScreen(page);
});

test("a cleared session cookie sends the owner to sign in when the page reloads", async ({ page }) => {
  await signUpAsNewOwner(page);
  const scanId = await createShopThroughApi(page, `Shop behind a cleared cookie ${Date.now()}`);
  await page.goto(`/shops/${scanId}`);
  await expect(page.getByRole("button", { name: "Delete this shop" })).toBeVisible();

  await page.context().clearCookies({ name: SESSION_COOKIE });
  await page.reload();
  await expectSignInScreen(page);
});
