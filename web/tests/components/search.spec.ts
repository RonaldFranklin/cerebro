import { expect, test, type Page } from "@playwright/test";

const note = { id: "sample", title: "Sample note", summary: "A synthetic note summary.", type: "guide", area: "research", priority: "P1", status: "archived" };
async function session(page: Page) {
  await page.route("**/api/session", route => route.fulfill({ json: { ceiling: "restricted", csrf_token: "fixture", absolute_remaining_seconds: 28800 } }));
  await page.route("**/api/graph?history=*", route => route.fulfill({ json: { nodes: [note], edges: [] } }));
}

test("keyboard search applies all visible filters and keeps terms out of history", async ({ page }) => {
  await session(page);
  await page.route("**/api/search?*", route => {
    const params = new URL(route.request().url()).searchParams;
    const matches = params.get("q") === "typed fixture" && Object.entries({
      area: "research", type: "guide", priority: "P1", status: "archived", history: "true",
    }).every(([key, value]) => params.get(key) === value);
    return route.fulfill({ json: { results: matches ? [note] : [] } });
  });
  await page.goto("/search");
  await expect(page.getByText("No matching notes.")).toBeVisible();
  await page.getByLabel("Include history").check();
  for (const [label, value] of Object.entries({ Area: "research", Type: "guide", Priority: "P1", Status: "archived" })) {
    await page.getByRole("combobox", {name: label, exact: true}).selectOption(value);
  }
  const query = page.getByRole("searchbox", {name: "Search notes"});
  await query.fill("typed fixture");
  await query.press("Enter");
  const result = page.getByRole("link", {name: "Sample note"});
  await expect(result).toHaveAttribute("href", "/notes/sample?history=true");
  await expect(page.getByText(note.summary)).toBeVisible();
  await expect(page).toHaveURL(/\/search$/);
  await result.click();
  await page.goBack();
  await expect(query).toHaveValue("typed fixture");
  await expect(result).toBeVisible();
});

test("search failure can be retried and oversized queries stay in the browser", async ({ page }) => {
  await session(page);
  let attempts = 0;
  await page.route("**/api/search?*", route => route.fulfill(++attempts === 1
    ? {status: 503, json: {}} : {json: {results: []}}));
  await page.goto("/search");
  await expect(page.getByText("Search is unavailable.")).toBeVisible();
  await page.getByRole("button", {name: "Retry search"}).click();
  await expect(page.getByText("No matching notes.")).toBeVisible();
  const input = page.getByRole("searchbox");
  await input.fill("é".repeat(513));
  await input.press("Enter");
  await expect(page.getByText("Search text must fit in 1,024 UTF-8 bytes.")).toBeVisible();
  expect(attempts).toBe(2);
});

test("submitting a new search cancels a slow, superseded request", async ({ page }) => {
  await session(page);
  let release!: () => void;
  await page.route("**/api/search?*", async route => {
    if (new URL(route.request().url()).searchParams.get("q") === "slow") {
      await new Promise<void>(resolve => { release = resolve; });
      await route.fulfill({json: {results: [note]}});
    } else await route.fulfill({json: {results: []}});
  });
  await page.goto("/search");
  const query = page.getByRole("searchbox");
  await query.fill("slow");
  await query.press("Enter");
  await expect.poll(() => Boolean(release)).toBe(true);
  await expect(page.getByText("Searching…")).toBeVisible();
  const aborted = page.waitForEvent("requestfailed", {predicate: r => r.url().includes("q=slow")});
  await query.fill("latest");
  await query.press("Enter");
  await aborted;
  release();
  await expect(page.getByText("No matching notes.")).toBeVisible();
  await expect(page.getByText(note.title)).toHaveCount(0);
});

test("sections load on demand and keep the historical note in scope", async ({page}) => {
  await session(page);
  await page.route("**/api/search?*", route => route.fulfill({
    json: {results: new URL(route.request().url()).searchParams.get("history") === "true" ? [note] : []},
  }));
  const reads: URL[] = [];
  await page.route("**/api/notes/**", route => {
    reads.push(new URL(route.request().url()));
    return route.fulfill({json: {...note, body: "## Build guide\n\nSynthetic steps."}});
  });
  await page.goto("/search");
  await page.getByLabel("Include history").check();
  await page.getByRole("button", {name: "Search notes"}).click();
  const sections = page.getByRole("button", {name: "Sections for Sample note"});
  await expect(sections).toHaveAttribute("aria-expanded", "false");
  expect(reads).toHaveLength(0);
  await sections.press("Enter");
  await expect(page.getByRole("link", {name: "Build guide"})).toHaveAttribute("href", "/notes/sample?history=true&section=Build+guide");
  expect(reads.map(url => url.search)).toEqual(["?history=true"]);
  await sections.click();
  await expect(page.getByRole("link", {name: "Build guide"})).toHaveCount(0);
});
