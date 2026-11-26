import { expect, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { test } from "./fixture";

async function signIn(page: Page, origin: string, password: string) {
  await page.goto(origin + "/health");
  const input = page.getByLabel("Password", {exact:true});
  await input.fill(password);
  await input.press("Enter");
}

test("private health findings link to their notes and stay off the public listener", async ({page, context, demo}) => {
  const violations: string[] = [];
  await page.exposeFunction("recordHealthViolation", (directive: string) => violations.push(directive));
  await page.addInitScript(() => document.addEventListener("securitypolicyviolation", event =>
    (window as unknown as {recordHealthViolation(directive:string):void}).recordHealthViolation(event.effectiveDirective)));
  const response = page.waitForResponse(res => new URL(res.url()).pathname === "/api/health");
  await signIn(page, demo.private_origin, demo.password);
  await expect(page.getByRole("heading", {name:"Health", exact:true})).toBeVisible();
  const broken = page.locator('a[href="/notes/broken-link-note?history=true"]').first();
  await expect(broken).toBeVisible();
  const health = await response;
  expect(health.status()).toBe(200);
  expect(health.headers()["cache-control"]).toContain("no-store");
  const report = await health.json();
  expect(report.search_index_checked).toBe(true);
  expect(report.issues).toEqual(expect.arrayContaining([
    expect.objectContaining({subject:"island-hub",code:"orphan_note",severity:"warning"}),
    expect.objectContaining({subject:"broken-link-note",code:"broken_link"}),
    expect.objectContaining({subject:"fixture-vault-catalog",code:"orphan_note"}),
  ]));
  await expect(page.locator('a[href="/notes/island-hub?history=true"]').first()).toBeVisible();
  await expect(page.locator('a[href="/notes/fixture-vault-catalog?history=true"]').first()).toBeVisible();
  if (process.env.CAPTURE_SCREENSHOTS === "1") {
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({
      path:fileURLToPath(new URL("../../../docs/screenshots/phase-5/11-health.png", import.meta.url)),
      fullPage:true, animations:"disabled",
    });
  }
  await broken.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading",{name:"Broken link note",exact:true})).toBeVisible();
  expect(violations).toEqual([]);

  const lower = await context.newPage();
  const requests: string[] = [];
  lower.on("request", req => {
    if (new URL(req.url()).pathname === "/api/health") requests.push(req.method());
  });
  await signIn(lower, demo.public_origin, demo.password);
  await expect(lower.getByText("This view requires the private listener.")).toBeVisible();
  await expect(lower.getByRole("link", {name:"Health", exact:true})).toHaveCount(0);
  expect(requests).toEqual([]);
  const denied = await lower.evaluate(async () => {
    const response = await fetch("/api/health", {headers:{"X-Forwarded-Host":"private.example.invalid","X-Forwarded-Proto":"https"}});
    return {status:response.status, text:await response.text()};
  });
  expect(denied.status).toBe(403);
  expect(denied.text).not.toContain("fixture-vault-catalog");
  await expect(lower.locator("body")).not.toContainText("fixture-vault-catalog");
});
