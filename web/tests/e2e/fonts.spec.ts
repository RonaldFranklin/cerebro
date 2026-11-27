import { expect } from "@playwright/test";
import { test } from "./fixture";

test("the three Latin fonts are served by the application under the production CSP", async ({page, demo}) => {
  const violations: string[] = [];
  await page.exposeFunction("recordFontViolation", (directive: string) => violations.push(directive));
  await page.addInitScript(() => document.addEventListener("securitypolicyviolation", event =>
    (window as unknown as {recordFontViolation(directive:string):void}).recordFontViolation(event.effectiveDirective)));
  const served: {origin: string; path: string; status: number; type: string}[] = [];
  page.on("response", response => {
    const url = new URL(response.url());
    if (url.pathname.endsWith(".woff2")) served.push({origin: url.origin, path: url.pathname,
      status: response.status(), type: response.headers()["content-type"]});
  });
  await page.goto(demo.private_origin);
  const password = page.getByLabel("Password", {exact:true});
  await password.fill(demo.password);
  await password.press("Enter");
  await expect(page.getByRole("heading", {name:"Overview", exact:true})).toBeVisible();
  await page.goto(demo.private_origin + "/notes/server-ssh-access");
  await expect(page.getByRole("heading", {name:"SSH access", exact:true})).toBeVisible();
  const loaded = () => page.evaluate(async () => {
    await document.fonts.ready;
    return [...document.fonts].filter(face => face.status === "loaded").map(face => face.family.replaceAll('"', "")).sort();
  });
  await expect.poll(loaded).toEqual(["Inter", "JetBrains Mono", "Space Grotesk"]);
  expect(await page.evaluate(() => document.fonts.size)).toBe(3);
  for (const file of served) {
    expect(file).toEqual({origin: demo.private_origin, status: 200, type: "font/woff2",
      path: expect.stringMatching(/^\/assets\/(inter|jetbrains-mono|space-grotesk)-latin-wght-normal-[\w-]+\.woff2$/)});
  }
  expect(new Set(served.map(file => file.path)).size).toBe(3);
  expect(violations).toEqual([]);
});
