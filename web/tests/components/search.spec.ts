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
  const input = page.getByRole("searchbox", {name: "Search notes"});
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
  const query = page.getByRole("searchbox", {name: "Search notes"});
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

test("search state outlives query garbage collection and clears on sign-out", async ({page}) => {
  await session(page);
  await page.clock.install();
  await page.route("**/api/search?*", route => route.fulfill({json: {results: []}}));
  await page.route("**/api/logout", route => route.fulfill({status: 204}));
  await page.route("**/api/login", route => route.fulfill({json: {ceiling: "restricted", csrf_token: "next", absolute_remaining_seconds: 28800}}));
  await page.goto("/search");
  const input = page.getByRole("searchbox", {name: "Search notes"});
  await input.fill("retain within session");
  await input.press("Enter");
  await page.getByRole("navigation", {name: "Workspace"}).getByRole("link", {name: "Graph"}).click();
  await page.clock.fastForward(6 * 60_000);
  await page.goBack();
  await expect(input).toHaveValue("retain within session");
  await page.getByRole("button", {name: "Sign out", exact: true}).click();
  await page.getByLabel("Password").fill("fixture");
  await page.getByRole("button", {name: "Sign in"}).click();
  await expect(input).toHaveValue("");
});

test("results announce successful completion and history-only filters leave the active search", async ({page}) => {
  await session(page);
  await page.route("**/api/graph?history=*", route => route.fulfill({
    json: {nodes: new URL(route.request().url()).searchParams.get("history") === "true" ? [note] : [], edges: []},
  }));
  const queries: URLSearchParams[] = [];
  await page.route("**/api/search?*", route => {
    const params = new URL(route.request().url()).searchParams;
    queries.push(params);
    return route.fulfill({json: {results: params.get("area") === "research" ? [note] : []}});
  });
  await page.goto("/search");
  const status = page.getByRole("region", {name: "Results"}).getByRole("status");
  await expect(status).toHaveText("No matching notes.");
  await status.evaluate(el => { el.setAttribute("data-marker", "persistent"); });
  await page.getByLabel("Include history").check();
  const area = page.getByRole("combobox", {name: "Area"});
  await area.selectOption("research");
  await page.getByRole("button", {name: "Search notes"}).click();
  await expect(status).toContainText("1 result shown.");
  await expect(status).toHaveAttribute("data-marker", "persistent");
  await page.getByLabel("Include history").uncheck();
  await expect(area).toHaveValue("");
  await expect(area.getByRole("option", {name: "research"})).toHaveCount(0);
  await page.getByRole("button", {name: "Search notes"}).click();
  await expect(status).toHaveText("No matching notes.");
  await expect.poll(() => queries.at(-1)?.get("history")).toBe("false");
  expect(queries.at(-1)?.has("area")).toBe(false);
});

test("missing metadata preserves the active filters and technical tokens", async ({page}) => {
  await session(page);
  await page.clock.install();
  const result = {...note, title: "Inspect git status", summary: "Open ./notes/demo.md"};
  let metadataAvailable = true;
  await page.route("**/api/graph?history=*", route => route.fulfill(metadataAvailable
    ? {json: {nodes: [note], edges: []}} : {status: 503, json: {}}));
  const requests: URLSearchParams[] = [];
  await page.route("**/api/search?*", route => {
    requests.push(new URL(route.request().url()).searchParams);
    return route.fulfill({json: {results: [result]}});
  });
  await page.goto("/search");
  const area = page.getByRole("combobox", {name: "Area"});
  await area.selectOption("research");
  await page.getByRole("button", {name: "Search notes"}).click();
  await expect.poll(() => requests.at(-1)?.get("area")).toBe("research");
  await page.getByRole("navigation", {name: "Workspace"}).getByRole("link", {name: "Graph"}).click();
  await page.clock.fastForward(6 * 60_000);
  metadataAvailable = false;
  await page.goBack();
  await expect(page.getByRole("button", {name: "Retry filters"})).toBeVisible();
  await expect(area).toHaveValue("research");
  const input = page.getByRole("searchbox", {name: "Search notes"});
  await input.fill("updated");
  await input.press("Enter");
  await expect.poll(() => requests.at(-1)?.get("q")).toBe("updated");
  expect(requests.at(-1)?.get("area")).toBe("research");
  await expect(page.getByText("git status", {exact: true})).toHaveAttribute("translate", "no");
  await expect(page.getByText("./notes/demo.md", {exact: true})).toHaveAttribute("translate", "no");
});

test("cached searches with equal result counts announce the new criteria", async ({page}) => {
  await session(page);
  await page.route("**/api/search?*", route => route.fulfill({json: {results: [note]}}));
  await page.goto("/search");
  const input = page.getByRole("searchbox", {name: "Search notes"});
  const status = page.getByRole("region", {name: "Results"}).getByRole("status");
  for (const text of ["alpha", "beta", "alpha"]) {
    await input.fill(text);
    await input.press("Enter");
    await expect(status).toContainText("1 result shown.");
    await expect(status).toContainText(text);
  }
});

test("editing history keeps displayed results bound to their submitted graph", async ({page}) => {
  await session(page);
  await page.route("**/api/graph?history=*", route => {
    const nodes = [note, {...note, id: "reference"}];
    if (new URL(route.request().url()).searchParams.get("history") === "true") nodes.push({...note, id: "archival"});
    return route.fulfill({json: {nodes, edges: []}});
  });
  await page.route("**/api/search?*", route => route.fulfill({
    json: {results: [{...note, summary: "Check reference and archival for context."}]},
  }));
  await page.goto("/search");
  const results = page.getByRole("region", {name: "Results"});
  const reference = results.getByText("reference", {exact: true});
  await expect(reference).toHaveAttribute("translate", "no");
  const graph = page.waitForResponse(r => r.url().endsWith("/api/graph?history=true"));
  await page.getByLabel("Include history").check();
  await graph;
  await expect(reference).toHaveAttribute("translate", "no");
  await expect(results.getByText("archival", {exact: true})).toHaveCount(0);
  await page.getByRole("button", {name: "Search notes"}).click();
  await expect(results.getByText("archival", {exact: true})).toHaveAttribute("translate", "no");
});
