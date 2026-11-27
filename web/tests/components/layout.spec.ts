import { expect, test, type Page } from "@playwright/test";

const overview = {core: [], core_words: 0, core_truncated: false, areas: ["research"], hubs: []};

async function owner(page: Page, ceiling = "restricted") {
  await page.route("**/api/session", route => route.fulfill({json: {ceiling, csrf_token: "fixture", absolute_remaining_seconds: 28800}}));
  await page.route("**/api/overview", route => route.fulfill({json: overview}));
  await page.route("**/api/graph?**", route => route.fulfill({json: {nodes: [], edges: []}}));
}

test("the icon rail names every destination and shows a tooltip on focus", async ({page}) => {
  await owner(page);
  await page.route("**/api/search?**", route => route.fulfill({json: {results: []}}));
  await page.goto("/");
  const rail = page.getByRole("navigation", {name: "Workspace"});
  await expect(rail.getByRole("link")).toHaveCount(4);
  for (const name of ["Graph", "Notes", "Recent changes", "Health"]) {
    const link = rail.getByRole("link", {name, exact: true});
    await expect(link).toHaveAttribute("aria-label", name);
    const box = await link.boundingBox();
    expect(Math.min(box!.width, box!.height)).toBeGreaterThanOrEqual(44);
  }
  const graph = rail.getByRole("link", {name: "Graph", exact: true});
  const tooltip = graph.locator(".rail-tooltip");
  await expect(tooltip).toBeHidden();
  await page.getByRole("link", {name: "Cérebro home"}).focus();
  await page.keyboard.press("Tab");
  await page.keyboard.press("Tab");
  await page.keyboard.press("Tab");
  await expect(graph).toBeFocused();
  await expect(tooltip).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(graph).toHaveAttribute("aria-current", "page");
});

test("note pages keep the Notes destination current", async ({page}) => {
  await owner(page);
  await page.route("**/api/notes/**", route => route.fulfill({status: 404, json: {}}));
  await page.goto("/notes/example-note");
  await expect(page.getByRole("link", {name: "Notes", exact: true})).toHaveAttribute("aria-current", "page");
});

test("the top bar shows the session ceiling and signs out", async ({page}) => {
  await owner(page);
  await page.route("**/api/logout", route => route.fulfill({status: 204}));
  await page.goto("/");
  const bar = page.getByRole("banner");
  await expect(bar.getByText("restricted · owner")).toBeVisible();
  await bar.getByRole("button", {name: "Sign out"}).click();
  await expect(page.getByLabel("Password")).toBeVisible();
});

test("slash focuses the top search, which runs on the search page and again from it", async ({page}) => {
  await owner(page);
  const queries: (string | null)[] = [];
  await page.route("**/api/search?**", route => {
    queries.push(new URL(route.request().url()).searchParams.get("q"));
    return route.fulfill({json: {results: []}});
  });
  await page.goto("/");
  await expect(page.getByRole("heading", {name: "Overview", exact: true})).toBeVisible();
  const search = page.getByRole("searchbox", {name: "Quick search"});
  await page.keyboard.press("/");
  await expect(search).toBeFocused();
  await expect(search).toHaveValue("");
  await search.fill("first/query");
  await search.press("Enter");
  await expect(page).toHaveURL(/\/search$/);
  const field = page.getByRole("searchbox", {name: "Search notes"});
  await expect(field).toHaveValue("first/query");
  await expect.poll(() => queries.at(-1)).toBe("first/query");
  await field.press("End");
  await page.keyboard.type("/typed");
  await expect(field).toHaveValue("first/query/typed");
  await search.fill("second");
  await search.press("Enter");
  await expect(field).toHaveValue("second");
  await expect.poll(() => queries.at(-1)).toBe("second");
});

test("a lower-ceiling session is told why a view is refused", async ({page}) => {
  await owner(page, "internal");
  let reads = 0;
  await page.route("**/api/health", route => { reads++; return route.fulfill({json: {}}); });
  await page.goto("/health");
  await expect(page.getByText("This view requires a restricted owner session. This session is limited to internal.")).toBeVisible();
  await expect(page.getByRole("banner").getByText("internal · owner")).toBeVisible();
  await expect(page.getByRole("navigation", {name: "Workspace"}).getByRole("link")).toHaveCount(2);
  expect(reads).toBe(0);
});

test("text and control colors keep their contrast on every surface", async ({page}) => {
  await owner(page);
  await page.goto("/");
  const colors = await page.evaluate(() => {
    const style = getComputedStyle(document.documentElement);
    const names = ["ground", "surface", "raised", "line-strong", "text", "muted", "accent", "on-accent", "success", "amber", "error"];
    return Object.fromEntries(names.map(name => [name, style.getPropertyValue(`--${name}`).trim()]));
  });
  const luminance = (hex: string) => {
    expect(hex).toMatch(/^#[0-9a-f]{6}$/i);
    const [r, g, b] = [1, 3, 5].map(at => parseInt(hex.slice(at, at + 2), 16) / 255)
      .map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
    return .2126 * r + .7152 * g + .0722 * b;
  };
  const contrast = (a: string, b: string) => {
    const [low, high] = [luminance(colors[a]), luminance(colors[b])].sort((x, y) => x - y);
    return (high + .05) / (low + .05);
  };
  for (const surface of ["ground", "surface", "raised"]) {
    for (const color of ["text", "muted", "accent", "success", "amber", "error"]) {
      expect(contrast(color, surface), `${color} on ${surface}`).toBeGreaterThanOrEqual(4.5);
    }
    expect(contrast("line-strong", surface), `control border on ${surface}`).toBeGreaterThanOrEqual(3);
  }
  expect(contrast("on-accent", "accent")).toBeGreaterThanOrEqual(4.5);
});

for (const path of ["/", "/search", "/graph"]) {
  test(`controls on ${path} are 44px targets and fit a narrow screen`, async ({page}) => {
    await owner(page);
    await page.route("**/api/search?**", route => route.fulfill({json: {results: []}}));
    await page.setViewportSize({width: 360, height: 780});
    await page.goto(path);
    await expect(page.getByRole("main").getByRole("heading", {level: 1})).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const small = await page.evaluate(() => [...document.querySelectorAll<HTMLElement>(
      "button, select, input:not([type=checkbox]):not([type=radio]), label:has(> input[type=checkbox]), label:has(> input[type=radio]), .topbar a, .rail a")]
      .filter(node => node.offsetParent !== null)
      .map(node => ({node: node.outerHTML.slice(0, 120), height: node.getBoundingClientRect().height}))
      .filter(item => item.height < 43.5));
    expect(small).toEqual([]);
  });
}
