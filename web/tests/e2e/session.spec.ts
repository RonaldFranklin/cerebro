import { expect, type Page } from "@playwright/test";
import { test } from "./fixture";

async function signIn(page: Page, password: string) {
  const field = page.getByLabel("Owner password", { exact: true });
  await field.focus();
  await field.fill(password);
  await field.press("Enter");
  await expect(page.getByRole("heading", { name: "Overview", exact: true })).toBeVisible();
}

test("owner sessions stay on their issuing HTTPS listener", async ({ page, context, demo }) => {
  await page.goto(demo.private_origin);
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
  await signIn(page, demo.password);
  const marker = "Synthetic vault catalog";
  const privateOverview = await page.evaluate(async () => (await fetch("/api/overview")).json());
  expect(privateOverview.hubs.some((hub: { title: string }) => hub.title === marker)).toBe(true);
  await expect(page.getByText(marker, { exact: true })).toBeVisible();
  const privateSession = await page.evaluate(async () => (await (await fetch("/api/session")).json()).ceiling);
  expect(privateSession).toBe("restricted");

  const publicPage = await context.newPage();
  await publicPage.goto(demo.public_origin);
  await expect(publicPage.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
  await signIn(publicPage, demo.password);
  const publicChecks = await publicPage.evaluate(async () => ({
    ceiling: (await (await fetch("/api/session")).json()).ceiling,
    healthStatus: (await fetch("/api/health")).status,
  }));
  expect(publicChecks).toEqual({ ceiling: "internal", healthStatus: 403 });
  const publicOverview = await publicPage.evaluate(async () => (await fetch("/api/overview")).json());
  expect(JSON.stringify(publicOverview).includes(marker)).toBe(false);
  expect(JSON.stringify(publicOverview).includes("fixture-vault-catalog")).toBe(false);
  await expect(publicPage.locator("body")).not.toContainText(marker);
  await expect(publicPage.locator("body")).not.toContainText("zebrafruit");

  const signOut = page.getByRole("button", { name: "Sign out", exact: true });
  await signOut.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByLabel("Owner password", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("Owner password", { exact: true })).toBeVisible();
  expect(await page.evaluate(async () => (await fetch("/api/session")).status)).toBe(401);
  expect(await publicPage.evaluate(async () => (await fetch("/api/session")).status)).toBe(200);
});
