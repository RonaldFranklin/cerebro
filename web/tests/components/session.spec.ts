import { expect, test } from "@playwright/test";

const session = { ceiling: "restricted", csrf_token: "s".repeat(43), absolute_remaining_seconds: 28800 };
const overview = {
  core: [{ id: "fixture", title: "Private fixture", summary: "Synthetic.", body: "Protected memory." }],
  areas: [], hubs: [], core_words: 2, core_truncated: false,
};

test("a revoked session removes cached content before another login", async ({ page }) => {
  let active = true;
  let version = 0;
  await page.route("**/api/session", route => route.fulfill(active
    ? { json: session } : { status: 401, json: { error: "Authentication required." } }));
  await page.route("**/api/overview", route => route.fulfill({
    json: version ? { ...overview, core: [] } : overview,
  }));
  await page.route("**/api/login", route => {
    active = true;
    version += 1;
    return route.fulfill({ json: { ...session, csrf_token: "t".repeat(43) } });
  });
  await page.goto("/");
  await expect(page.getByText("Private fixture", { exact: true })).toBeVisible();
  active = false;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByLabel("Password")).toBeVisible();
  await expect(page.getByText("Private fixture", { exact: true })).toHaveCount(0);
  await page.getByLabel("Password").fill("fixture");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview", exact: true })).toBeVisible();
  await expect(page.getByText("Private fixture", { exact: true })).toHaveCount(0);
});

test("a failed logout hides the workspace and offers an authenticated retry", async ({ page }) => {
  let attempts = 0;
  await page.route("**/api/session", route => route.fulfill({ json: session }));
  await page.route("**/api/overview", route => route.fulfill({ json: overview }));
  await page.route("**/api/logout", route => {
    expect(route.request().headers()["x-csrf-token"]).toBe(session.csrf_token);
    return route.fulfill(++attempts === 1 ? { status: 503, json: {} } : { status: 204 });
  });
  await page.goto("/");
  await expect(page.getByText("Private fixture", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByText("Private fixture", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Retry sign out" }).click();
  await expect(page.getByLabel("Password")).toBeVisible();
});

test("a protected deep link survives keyboard login", async ({ page }) => {
  await page.route("**/api/session", route => route.fulfill({ status: 401, json: {} }));
  await page.route("**/api/login", route => {
    expect(route.request().postDataJSON()).toEqual({password: "fixture"});
    expect(route.request().headers()["x-cerebro-request"]).toBe("1");
    return route.fulfill({ json: session });
  });
  await page.goto("/graph");
  const password = page.getByLabel("Password");
  await password.focus();
  await password.fill("fixture");
  await password.press("Enter");
  await expect(page.getByRole("heading", { name: "Graph", exact: true })).toBeVisible();
  await expect(page).toHaveURL(/\/graph$/);
});

test("protected content stays hidden while a resumed session is checked", async ({ page }) => {
  let resume!: () => void;
  let checking = false;
  await page.route("**/api/session", async route => {
    if (checking) await new Promise<void>(resolve => { resume = resolve; });
    await route.fulfill({ json: session });
  });
  await page.route("**/api/overview", route => route.fulfill({ json: overview }));
  await page.goto("/");
  const note = page.getByText("Private fixture", { exact: true });
  await expect(note).toBeVisible();
  checking = true;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByText("Checking session…")).toBeVisible();
  await expect(note).toBeHidden();
  resume();
  await expect(note).toBeVisible();
});

test("restored sessions expire at their remaining server lifetime", async ({ page }) => {
  await page.clock.install();
  await page.route("**/api/session", route => route.fulfill({ json: { ...session, absolute_remaining_seconds: 60 } }));
  await page.route("**/api/overview", route => route.fulfill({ json: overview }));
  await page.goto("/");
  await expect(page.getByText("Private fixture", { exact: true })).toBeVisible();
  await page.clock.fastForward(61000);
  await expect(page.getByLabel("Password")).toBeVisible();
});

test("retry signs out the current cookie after a cross-tab session change", async ({ page }) => {
  let csrf = session.csrf_token;
  await page.route("**/api/session", route => route.fulfill({ json: { ...session, csrf_token: csrf } }));
  await page.route("**/api/overview", route => route.fulfill({ json: overview }));
  await page.route("**/api/logout", route => route.fulfill(
    route.request().headers()["x-csrf-token"] === csrf ? { status: 204 } : { status: 403 }));
  await page.goto("/");
  await expect(page.getByText("Private fixture", { exact: true })).toBeVisible();
  csrf = "t".repeat(43);
  await page.getByRole("button", { name: "Sign out" }).click();
  await page.getByRole("button", { name: "Retry sign out" }).click();
  await expect(page.getByLabel("Password")).toBeVisible();
});
