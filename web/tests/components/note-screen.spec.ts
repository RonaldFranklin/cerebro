import { expect, test, type Page } from "@playwright/test";

const summary = {type: "guide", area: "projects", priority: "P2", status: "current", sensitivity: "internal"};
const nodes = [
  {...summary, id: "plan", title: "Status and roadmap", summary: "Plan.", type: "project"},
  {...summary, id: "cerebro", title: "Cerebro", summary: "Hub.", type: "hub", priority: "P1"},
  {...summary, id: "flow", title: "Development workflow", summary: "Flow."},
];
const edges = [
  {source: "plan", target: "cerebro", kind: "hub", section: null},
  {source: "plan", target: "cerebro", kind: "wikilink", section: null},
  {source: "plan", target: "flow", kind: "wikilink", section: null},
  {source: "cerebro", target: "plan", kind: "wikilink", section: null},
  {source: "cerebro", target: "flow", kind: "wikilink", section: null},
];
const note = {...nodes[0], hub: "cerebro", related: [], superseded_by: null, lang: "en", created: "2026-11-01",
  updated: "2026-11-27", review_by: "2027-01-01", members: null, section: null, body: "## Next task\n\nBody.", content_hash: "a".repeat(64)};

async function owner(page: Page, neighbors = 200) {
  await page.route("**/api/session", route => route.fulfill({json: {ceiling: "restricted", csrf_token: "f", absolute_remaining_seconds: 28800}}));
  await page.route("**/api/graph?*", route => route.fulfill({json: {nodes, edges, center: null}}));
  await page.route("**/api/notes/plan**", route => route.fulfill({json: note}));
  let reads = 0;
  await page.route("**/api/notes/plan/neighbors**", route => ++reads === 1 && neighbors !== 200
    ? route.fulfill({status: neighbors, json: {}}) : route.fulfill({json: {nodes, edges, center: "plan"}}));
}

test("the note shows its trail, properties, neighborhood and links in both directions", async ({page}) => {
  await owner(page);
  await page.goto("/notes/plan");
  const trail = page.getByRole("navigation", {name: "Breadcrumb"});
  await expect(trail).toContainText("projects");
  await expect(trail.getByRole("link", {name: "Cerebro"})).toHaveAttribute("href", "/notes/cerebro");
  await expect(trail.locator("[aria-current=page]")).toHaveText("Status and roadmap");
  await expect(page.getByRole("main").getByText("plan", {exact: true})).toHaveAttribute("translate", "no");
  const properties = page.getByRole("region", {name: "Note properties"});
  for (const text of ["project · P2", "internal", "current", "2026-11-27", "2027-01-01"]) await expect(properties).toContainText(text);
  await expect(page.getByRole("link", {name: "Edit note"})).toHaveAttribute("href", "/editor/plan");
  const out = page.getByRole("region", {name: "Links out"});
  await expect(out.getByRole("heading")).toHaveText("Links out · 2");
  await expect(out.getByRole("link")).toHaveText(["Cerebro", "Development workflow"]);
  const back = page.getByRole("region", {name: "Linked from"});
  await expect(back.getByRole("heading")).toHaveText("Linked from · 1");
  await expect(back.getByRole("link", {name: "Cerebro"})).toHaveAttribute("href", "/notes/cerebro");
  const map = page.getByRole("region", {name: "Neighborhood"});
  await expect(map.locator("svg circle[data-note]")).toHaveCount(3);
  await expect(map.locator("svg line")).toHaveCount(2);
});

test("the note id can be copied", async ({page, context}) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await owner(page);
  await page.goto("/notes/plan");
  await page.getByRole("button", {name: "Copy note id"}).click();
  await expect(page.getByRole("status").filter({hasText: "Note id copied."})).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe("plan");
});

test("a failed neighborhood read leaves the note readable and can be retried", async ({page}) => {
  await owner(page, 503);
  await page.goto("/notes/plan");
  await expect(page.getByText("Body.")).toBeVisible();
  await expect(page.getByText("Links are unavailable.")).toBeVisible();
  await page.getByRole("button", {name: "Retry links"}).click();
  await expect(page.getByRole("region", {name: "Links out"}).getByRole("link")).toHaveCount(2);
});

test("the note fits a narrow screen with the side panels below", async ({page}) => {
  await owner(page);
  await page.setViewportSize({width: 360, height: 780});
  await page.goto("/notes/plan");
  await expect(page.getByRole("region", {name: "Links out"})).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const body = (await page.getByText("Body.").boundingBox())!;
  expect((await page.getByRole("region", {name: "Neighborhood"}).boundingBox())!.y).toBeGreaterThan(body.y);
  for (const link of await page.getByRole("region", {name: "Links out"}).getByRole("link").all()) {
    expect((await link.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  }
});
