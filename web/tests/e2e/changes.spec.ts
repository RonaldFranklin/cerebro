import { expect } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { test } from "./fixture";

test("the private owner can trace the agent update from history to its note", async ({page, context, demo}) => {
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
  if (process.env.CAPTURE_SCREENSHOTS === "1") {
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({path:fileURLToPath(new URL("../../../docs/screenshots/phase-7/recent-changes.png", import.meta.url)),
      fullPage:true, animations:"disabled"});
  }
  await page.getByRole("link", {name:"Open note"}).click();
  await expect(page.getByRole("heading", {name:"Island hub",exact:true})).toBeVisible();
  await expect(page.getByText("Collect the infrastructure handover notes here.", {exact:false}).first()).toBeVisible();

  const lower = await context.newPage();
  await lower.goto(demo.public_origin + "/changes");
  await lower.getByLabel("Password", {exact:true}).fill(demo.password);
  await lower.getByLabel("Password", {exact:true}).press("Enter");
  await expect(lower.getByText("This view requires the private listener.")).toBeVisible();
  await expect(lower.getByRole("navigation").getByRole("link", {name:"Recent changes"})).toHaveCount(0);
  for (const path of ["/api/changes",new URL(detail.url()).pathname]) {
    const status = await lower.evaluate(async path => (await fetch(path)).status, path);
    expect(status).toBe(403);
  }
});
