import { expect, test } from "@playwright/test";

const overview = {
  core: [{ id: "example-note", title: "Example note", summary: "A fixture summary.", body: "Fixture body." }],
  core_words: 2,
  core_truncated: false,
  areas: ["research"],
  hubs: [{ id: "research-hub", title: "Research hub", summary: "Linked research notes." }],
};

test("owner overview links visible content and exposes private navigation", async ({ page }) => {
  await page.route("**/api/session", route => route.fulfill({ json: { ceiling: "restricted", csrf_token: "test", absolute_remaining_seconds: 28800 } }));
  await page.route("**/api/overview", route => route.fulfill({ json: overview }));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Overview", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Example note" })).toHaveAttribute("href", "/notes/example-note");
  await expect(page.getByRole("link", { name: "Research hub" })).toHaveAttribute("href", "/notes/research-hub");
  await expect(page.getByRole("navigation").getByRole("link", { name: "Health" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Overview", exact: true })).toHaveAttribute("aria-current", "page");
});

test("lower-ceiling navigation omits private destinations and empty overview shows useful states", async ({ page }) => {
  await page.route("**/api/session", route => route.fulfill({ json: { ceiling: "internal", csrf_token: "test", absolute_remaining_seconds: 28800 } }));
  await page.route("**/api/overview", route => route.fulfill({ json: { ...overview, core: [], hubs: [], areas: [] } }));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Overview", exact: true })).toBeVisible();
  await expect(page.getByRole("navigation").getByRole("link", { name: "Inbox" })).toHaveCount(0);
  await expect(page.getByRole("navigation").getByRole("link", { name: "Health" })).toHaveCount(0);
  await expect(page.getByText("No core notes are visible.")).toBeVisible();
});

test("login provides a labelled password field and keyboard submission", async ({ page }) => {
  await page.route("**/api/session", route => route.fulfill({ status: 401, json: {} }));
  await page.goto("/");
  const password = page.getByLabel("Password", { exact: true });
  await expect(password).toHaveAttribute("type", "password");
  await password.focus();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Sign in" })).toBeFocused();
});
