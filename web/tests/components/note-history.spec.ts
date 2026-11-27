import { expect, test, type Page } from "@playwright/test";

test.use({timezoneId: "UTC"});

const note = {id: "plan", title: "Plan", summary: "Plan.", type: "project", area: "projects", priority: "P2", status: "current",
  sensitivity: "internal", hub: null, related: [], superseded_by: null, lang: "en", created: "2026-11-01",
  updated: "2026-11-27", review_by: null, members: null, section: null, body: "Body."};
const change = {revision: "a".repeat(40), note_id: "plan", title: "Plan", actor: "owner",
  timestamp: "2026-11-27T12:00:00+00:00", operation: "update"};

async function reader(page: Page, ceiling = "restricted") {
  await page.route("**/api/session", route => route.fulfill({json: {ceiling, csrf_token: "f", absolute_remaining_seconds: 28800}}));
  await page.route("**/api/graph?*", route => route.fulfill({json: {nodes: [note], edges: [], center: null}}));
  await page.route("**/api/notes/plan**", route => route.fulfill({json: note}));
  await page.route("**/api/notes/plan/neighbors**", route => route.fulfill({json: {nodes: [note], edges: [], center: "plan"}}));
}

test("the note lists its changes and loads older ones", async ({page}) => {
  await reader(page);
  const older = {...change, revision: "b".repeat(40), actor: "codex", operation: "create", timestamp: "2026-11-20T12:00:00+00:00"};
  await page.route("**/api/notes/plan/changes**", route => {
    const cursor = new URL(route.request().url()).searchParams.get("cursor");
    return route.fulfill({json: cursor ? {changes: [older], next_cursor: null} : {changes: [change], next_cursor: "more"}});
  });
  await page.goto("/notes/plan");
  const history = page.getByRole("region", {name: "History"});
  await expect(history.getByRole("listitem")).toHaveText(["Updated by Owner2026-11-27 · aaaaaaa"]);
  await history.getByRole("button", {name: "Load older changes"}).click();
  await expect(history.getByRole("listitem")).toHaveCount(2);
  await expect(history.getByRole("listitem").nth(1)).toContainText("Created by codex");
  await expect(history.getByRole("button", {name: "Load older changes"})).toHaveCount(0);
  await expect(history.getByRole("link", {name: "View all changes"})).toHaveAttribute("href", "/changes");
});

test("an empty window says so and can look further back", async ({page}) => {
  await reader(page);
  await page.route("**/api/notes/plan/changes**", route => {
    const cursor = new URL(route.request().url()).searchParams.get("cursor");
    return route.fulfill({json: {changes: [], next_cursor: cursor ? null : "more"}});
  });
  await page.goto("/notes/plan");
  const history = page.getByRole("region", {name: "History"});
  await expect(history).toContainText("No changes to this note in the recent window.");
  await history.getByRole("button", {name: "Load older changes"}).click();
  await expect(history).toContainText("No changes to this note.");
});

test("a failed history read leaves the note readable and can be retried", async ({page}) => {
  await reader(page);
  let reads = 0;
  await page.route("**/api/notes/plan/changes**", route => ++reads === 1
    ? route.fulfill({status: 503, json: {}}) : route.fulfill({json: {changes: [change], next_cursor: null}}));
  await page.goto("/notes/plan");
  await expect(page.getByText("Body.")).toBeVisible();
  await page.getByRole("button", {name: "Retry history"}).click();
  await expect(page.getByRole("region", {name: "History"}).getByRole("listitem")).toHaveCount(1);
});

test("a lower-ceiling session never asks for history", async ({page}) => {
  await reader(page, "internal");
  let reads = 0;
  await page.route("**/api/notes/plan/changes**", route => { reads++; return route.fulfill({json: {changes: [], next_cursor: null}}); });
  await page.goto("/notes/plan");
  await expect(page.getByText("Body.")).toBeVisible();
  await expect(page.getByRole("region", {name: "History"})).toHaveCount(0);
  expect(reads).toBe(0);
});
