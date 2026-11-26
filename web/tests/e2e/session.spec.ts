import { expect, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { test } from "./fixture";

async function screenshot(page: Page, filename: string) {
  if (process.env.CAPTURE_SCREENSHOTS !== "1") return;
  const directory = new URL("../../../docs/screenshots/phase-5/", import.meta.url);
  await mkdir(directory, { recursive: true });
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: fileURLToPath(new URL(filename, directory)), animations: "disabled" });
}

async function signIn(page: Page, password: string) {
  const field = page.getByLabel("Password", { exact: true });
  await field.focus();
  await field.fill(password);
  await field.press("Enter");
  await expect(page.getByRole("heading", { name: "Overview", exact: true })).toBeVisible();
}

test("owner sessions stay on their issuing HTTPS listener", async ({ page, context, demo }) => {
  await page.goto(demo.private_origin);
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
  await screenshot(page, "07-login.png");
  await signIn(page, demo.password);
  const marker = "Synthetic vault catalog";
  const privateOverview = await page.evaluate(async () => (await fetch("/api/overview")).json());
  expect(privateOverview.hubs.some((hub: { title: string }) => hub.title === marker)).toBe(true);
  await expect(page.getByText(marker, { exact: true })).toBeVisible();
  await screenshot(page, "07-overview.png");
  const privateSession = await page.evaluate(async () => (await (await fetch("/api/session")).json()).ceiling);
  expect(privateSession).toBe("restricted");

  const publicPage = await context.newPage();
  await publicPage.goto(demo.public_origin);
  await expect(publicPage.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
  await signIn(publicPage, demo.password);
  const publicChecks = await publicPage.evaluate(async () => ({
    ceiling: (await (await fetch("/api/session")).json()).ceiling,
    inboxStatus: (await fetch("/api/inbox")).status,
  }));
  expect(publicChecks).toEqual({ ceiling: "internal", inboxStatus: 403 });
  const publicOverview = await publicPage.evaluate(async () => (await fetch("/api/overview")).json());
  expect(JSON.stringify(publicOverview).includes(marker)).toBe(false);
  expect(JSON.stringify(publicOverview).includes("fixture-vault-catalog")).toBe(false);
  await expect(publicPage.locator("body")).not.toContainText(marker);
  await expect(publicPage.locator("body")).not.toContainText("zebrafruit");

  const signOut = page.getByRole("button", { name: "Sign out", exact: true });
  await signOut.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByLabel("Password", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("Password", { exact: true })).toBeVisible();
  expect(await page.evaluate(async () => (await fetch("/api/session")).status)).toBe(401);
  expect(await publicPage.evaluate(async () => (await fetch("/api/session")).status)).toBe(200);
});
