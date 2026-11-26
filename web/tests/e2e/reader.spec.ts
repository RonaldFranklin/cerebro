import { expect } from "@playwright/test";
import { test } from "./fixture";

test("search, inspect sections and follow visible hub members", async ({ page, demo }) => {
  const violations: string[] = [];
  await page.exposeFunction("recordPolicyViolation", (directive: string) => violations.push(directive));
  await page.addInitScript(() => {
    document.addEventListener("securitypolicyviolation", event =>
      (window as unknown as {recordPolicyViolation: (directive: string) => void}).recordPolicyViolation(event.effectiveDirective)
    );
  });
  await page.goto(demo.private_origin + "/search");
  const password = page.getByLabel("Password", { exact: true });
  await password.fill(demo.password);
  await password.press("Enter");
  const results = page.getByRole("region", { name: "Results" });
  await expect(results.getByRole("link", { name: "SSH access" })).toBeVisible();
  const search = page.getByRole("searchbox", { name: "Search notes" });
  await search.fill("SSH");
  await search.press("Enter");
  const matched = results.getByRole("link", { name: "SSH access" });
  await matched.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", {name: "SSH access", exact: true})).toBeVisible();
  await expect(page.locator("pre code")).toHaveText(/ssh -V/);
  const sections = page.getByRole("navigation", {name:"Sections"});
  await sections.getByRole("link", { name:"Configuration", exact:true }).click();
  await expect(page.getByRole("link", {name:"Read full note"})).toBeVisible();
  await expect(page.locator("pre code")).toHaveCount(0);
  await page.getByRole("link", {name:"Read full note"}).click();
  await expect(page.locator("pre code")).toHaveCount(1);
  await page.getByRole("navigation", {name:"Breadcrumb"}).getByRole("link", {name:"infrastructure"}).click();
  const members = page.getByRole("region", {name:"Hub members"});
  await expect(members.getByRole("link", {name:"SSH access"})).toBeVisible();
  await members.getByRole("link", {name:"Docker conventions"}).click();
  await expect(page.getByRole("heading", {name:"Docker conventions", exact:true})).toBeVisible();
  await page.setViewportSize({width:390,height:844});
  await expect(page.getByRole("heading", {name:"Docker conventions", exact:true})).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(violations).toEqual([]);
});
