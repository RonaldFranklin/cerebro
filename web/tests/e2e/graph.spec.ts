import { expect, type Page } from "@playwright/test";
import { test } from "./fixture";

async function signIn(page: Page, origin: string, password: string) {
  await page.goto(origin + "/graph");
  const input = page.getByLabel("Password", {exact:true});
  await input.fill(password);
  await input.press("Enter");
  await expect(page.getByRole("heading", {name:"Graph", exact:true})).toBeVisible();
}

test("the production worker lays out a selectable WebGL graph under the served CSP", async ({page, demo}) => {
  const violations: string[] = [];
  await page.exposeFunction("recordGraphViolation", (directive: string) => violations.push(directive));
  await page.addInitScript(() => {
    document.addEventListener("securitypolicyviolation", event => {
      (window as unknown as {recordGraphViolation(directive:string):void}).recordGraphViolation(event.effectiveDirective);
    });
  });
  const worker = page.waitForEvent("worker");
  await signIn(page, demo.private_origin, demo.password);
  const workerUrl = new URL((await worker).url());
  expect(workerUrl.origin).toBe(demo.private_origin);
  expect(workerUrl.pathname).toMatch(/^\/assets\/.+\.js$/);
  const surface = page.locator("[data-renderer='ready'] .sigma-surface");
  await expect(surface).toBeVisible();
  expect(await surface.locator("canvas").evaluateAll(items => items.some(canvas =>
    canvas instanceof HTMLCanvasElement && (canvas.getContext("webgl2") || canvas.getContext("webgl"))))).toBe(true);
  await expect(page.getByRole("button", {name:"Explore Synthetic vault catalog"})).toBeVisible();
  await expect(page.getByRole("combobox", {name:"Area",exact:true}).getByRole("option", {name:"security", exact:true})).toHaveCount(1);
  await page.getByRole("button", {name:"Explore Infrastructure", exact:true}).click();
  await expect(page.getByRole("button", {name:"Back to global graph"})).toBeVisible();
  await expect(surface).toBeVisible();
  await page.getByRole("combobox", {name:"Color by",exact:true}).selectOption("type");
  await expect(page.getByText("Color by type", {exact:true})).toBeVisible();
  await expect(surface).toBeVisible();
  await page.getByRole("button", {name:"Back to global graph"}).click();
  await page.getByRole("combobox", {name:"Priority", exact:true}).selectOption("P0");
  await expect(page.getByRole("button", {name:/^Explore /})).toHaveCount(1);
  await expect(surface).toBeVisible();
  await surface.click();
  await expect(page.getByRole("button", {name:"Back to global graph"})).toBeVisible();
  await page.getByRole("link", {name:"Core rules", exact:true}).click();
  await expect(page.getByRole("heading", {name:"Core rules", exact:true})).toBeVisible();
  expect(violations).toEqual([]);
});

test("the public graph stays navigable without WebGL and omits restricted notes", async ({page, demo}) => {
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function(this: HTMLCanvasElement, ...args: Parameters<typeof original>) {
      if (String(args[0]).includes("webgl")) return null;
      return Reflect.apply(original, this, args);
    } as typeof original;
  });
  const graphResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/api/graph");
  await signIn(page, demo.public_origin, demo.password);
  const data = await (await graphResponse).text();
  expect(data).toContain("infrastructure");
  expect(data).not.toContain("fixture-vault-catalog");
  await expect(page.locator("[data-renderer='fallback']")).toBeVisible();
  await expect(page.locator("body")).not.toContainText("Synthetic vault catalog");
  await expect(page.getByRole("combobox",{name:"Area",exact:true}).getByRole("option", {name:"security", exact:true})).toHaveCount(0);
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const explore = page.getByRole("button", {name:"Explore Infrastructure",exact:true});
  await explore.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("radio",{name:"1 hop", exact:true})).toBeChecked();
  await page.getByRole("radio",{name:"2 hops", exact:true}).check();
  await page.getByRole("link",{name:"Infrastructure", exact:true}).click();
  await expect(page.getByRole("heading",{name:"Infrastructure",exact:true})).toBeVisible();
});
