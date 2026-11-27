import { expect, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { test } from "./fixture";

test.use({ publicBrowserCeiling: "restricted" });

async function signIn(page: Page, url: string, password: string) {
  await page.goto(url);
  const input = page.getByLabel("Owner password", {exact:true});
  await input.fill(password);
  await input.press("Enter");
}

test("the opted-in public listener gives the owner health, history and restricted notes", async ({page, demo}) => {
  await signIn(page, demo.public_origin + "/health", demo.password);
  await expect(page.getByRole("heading", {name:"Health", exact:true})).toBeVisible();
  await expect(page.locator('a[href="/notes/fixture-vault-catalog?history=true"]').first()).toBeVisible();
  expect(await page.evaluate(async () => (await (await fetch("/api/session")).json()).ceiling)).toBe("restricted");
  await page.getByRole("navigation").getByRole("link", {name:"Recent changes"}).click();
  await expect(page.getByRole("button", {name:/Island hub.*fixture-agent/})).toBeVisible();
  for (const path of ["/api/health", "/api/changes"]) {
    const status = await page.evaluate(async path => (await fetch(path, {credentials:"omit"})).status, path);
    expect(status).toBe(401);
  }
});

test("the owner edits a note through the opted-in public listener", async ({page, demo}) => {
  await signIn(page, demo.public_origin + "/editor/server-ssh-access", demo.password);
  const summary = "Edited by the owner through the public listener of this disposable fixture.";
  await page.getByLabel("Summary",{exact:true}).fill(summary);
  const saved = page.waitForResponse(res => res.request().method() === "PUT");
  await page.getByRole("button",{name:"Save & commit",exact:true}).click();
  expect((await saved).status()).toBe(200);
  await expect(page.getByText("Changes saved.",{exact:true})).toBeVisible();
  const message = execFileSync("git",["-C",demo.content_repo,"log","-1","--format=%B"],{encoding:"utf8"});
  expect(message).toContain("Written-by: owner");
});
