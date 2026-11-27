import { expect } from "@playwright/test";
import { test } from "./fixture";

test("the owner can trace the agent update from history to its note", async ({page, context, demo}) => {
  await page.goto(demo.private_origin);
  const password = page.getByLabel("Password", {exact:true});
  await password.fill(demo.password);
  await password.press("Enter");
  await page.getByRole("navigation").getByRole("link", {name:"Recent changes"}).click();
  await expect(page).toHaveURL(demo.private_origin + "/changes");
  const button = page.getByRole("button", {name:/Island hub.*fixture-agent/});
  await expect(button).toContainText("Updated");
  const timestamp = await button.locator("time").getAttribute("datetime");
  expect(Number.isFinite(Date.parse(timestamp ?? ""))).toBe(true);
  await button.focus();
  const response = page.waitForResponse(res => /^\/api\/changes\/[0-9a-f]+$/.test(new URL(res.url()).pathname));
  await page.keyboard.press("Enter");
  const detail = await response;
  expect(detail.headers()["cache-control"]).toContain("no-store");
  await expect(page.getByRole("region", {name:"Commit diff"})).toContainText("Collect the infrastructure handover notes here.");
  await page.getByRole("link", {name:"Open note"}).click();
  await expect(page.getByRole("heading", {name:"Island hub",exact:true})).toBeVisible();
  await expect(page.getByText("Collect the infrastructure handover notes here.", {exact:false}).first()).toBeVisible();

  const remote = await context.newPage();
  await remote.goto(demo.public_origin + "/changes");
  await remote.getByLabel("Password", {exact:true}).fill(demo.password);
  await remote.getByLabel("Password", {exact:true}).press("Enter");
  await expect(remote.getByRole("button", {name:/Island hub.*fixture-agent/})).toBeVisible();
  await expect(remote.getByRole("navigation").getByRole("link", {name:"Recent changes"})).toBeVisible();
  for (const path of ["/api/changes",new URL(detail.url()).pathname]) {
    const status = await remote.evaluate(async path => (await fetch(path, {credentials:"omit"})).status, path);
    expect(status).toBe(401);
  }
});
