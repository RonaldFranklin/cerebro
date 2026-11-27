import { expect, test, type Page } from "@playwright/test";

const guide = {id:"guide", title:"Field guide", area:"research", type:"guide", priority:"P1", status:"verified", sensitivity:"internal", summary:"Guide."};
const hub = {...guide, id:"catalog", title:"Catalog", type:"hub", priority:"P0"};
const archived = {...guide, id:"archive", title:"Archive", status:"archived", area:"archive", sensitivity:"restricted"};
const edge = {source:"guide", target:"catalog", kind:"related", section:null};

async function session(page: Page) {
  await page.route("**/api/session", route => route.fulfill({json:{ceiling:"restricted",csrf_token:"fixture",absolute_remaining_seconds:28800}}));
}

test("global filters, keyboard exploration, hops and return preserve graph context", async ({page}) => {
  await session(page);
  await page.route("**/api/graph**", route => {
    const params = new URL(route.request().url()).searchParams;
    const candidates = params.get("history") === "true" ? [guide, hub, archived] : [guide, hub];
    const nodes = candidates.filter(node => Object.entries(node).every(([key,value]) => !params.has(key) || params.get(key) === value));
    return route.fulfill({json:{nodes, edges:nodes.includes(guide) && nodes.includes(hub) ? [edge] : [], center:null}});
  });
  await page.route("**/api/notes/guide/neighbors**", route => {
    const hops = new URL(route.request().url()).searchParams.get("hops");
    return route.fulfill({json:{nodes:hops === "2" ? [guide, hub, archived] : [guide,hub], edges:[edge], center:"guide"}});
  });
  await page.goto("/graph");
  const select = (name: string) => page.getByRole("combobox", {name, exact:true});
  const explore = (title: string) => page.getByRole("button", {name:"Explore " + title, exact:true});
  await expect(explore("Field guide")).toBeVisible();
  await expect(explore("Archive")).toHaveCount(0);
  await expect(page.getByText(/2 notes · 1 links?/)).toBeVisible();
  await page.getByLabel("Include history").check();
  await expect(explore("Archive")).toBeVisible();
  for (const [key, value, excluded] of [
    ["Area", "research", "Archive"], ["Type", "guide", "Catalog"], ["Priority", "P1", "Catalog"],
    ["Status", "verified", "Archive"], ["Sensitivity", "internal", "Archive"],
  ]) {
    await select(key).selectOption(value);
    await expect(explore("Field guide")).toBeVisible();
    await expect(explore(excluded)).toHaveCount(0);
    await select(key).selectOption("");
    await expect(explore(excluded)).toBeVisible();
  }
  await select("Color by").selectOption("type");
  for (const [key,value] of Object.entries({Area:"research", Type:"guide", Priority:"P1", Status:"verified", Sensitivity:"internal"})) {
    await select(key).selectOption(value);
  }
  await expect(explore("Catalog")).toHaveCount(0);
  await expect(explore("Archive")).toHaveCount(0);
  await expect(page.getByText(/1 notes? · 0 links/)).toBeVisible();
  await explore("Field guide").focus();
  const local = page.waitForResponse(response => new URL(response.url()).pathname === "/api/notes/guide/neighbors");
  await page.keyboard.press("Enter");
  expect(Object.fromEntries(new URL((await local).url()).searchParams)).toEqual({
    hops:"1", history:"true", area:"research", type:"guide", priority:"P1", status:"verified", sensitivity:"internal",
  });
  await expect(page.getByRole("radio",{name:"1 hop",exact:true})).toBeChecked();
  for (const key of ["Area", "Type", "Priority", "Status", "Sensitivity"]) await select(key).selectOption("");
  await expect(explore("Catalog")).toBeVisible();
  await expect(explore("Archive")).toHaveCount(0);
  const more = page.waitForResponse(response => response.url().includes("/neighbors?") && new URL(response.url()).searchParams.get("hops") === "2");
  await page.getByRole("radio",{name:"2 hops",exact:true}).check();
  await more;
  await expect(explore("Archive")).toBeVisible();
  await expect(page.getByRole("link", {name:"Field guide", exact:true})).toHaveAttribute("href", "/notes/guide?history=true");
  await page.getByRole("button",{name:"Back to global graph"}).click();
  for (const [key,value] of Object.entries({Area:"research", Type:"guide", Priority:"P1", Status:"verified", Sensitivity:"internal", "Color by":"type"})) {
    await expect(select(key)).toHaveValue(value);
  }
  await expect(page.getByLabel("Include history")).toBeChecked();
  await expect(explore("Catalog")).toHaveCount(0);
});

test("the HTML neighborhood follows two-hop connections through matching nodes only", async ({page}) => {
  await session(page);
  await page.addInitScript(() => {
    const get = HTMLCanvasElement.prototype.getContext;
    Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
      value(this: HTMLCanvasElement, kind: string, ...args: unknown[]) {
        return kind.includes("webgl") ? null : Reflect.apply(get, this, [kind, ...args]);
      },
    });
  });
  const entry = {...guide, id:"entry", title:"Entry"};
  const bridge = {...guide, id:"bridge", title:"Bridge", type:"hub"};
  const destination = {...guide, id:"destination", title:"Destination"};
  const first = {...edge, source:"entry", target:"bridge", kind:"related"};
  const second = {...edge, source:"bridge", target:"destination", kind:"wikilink"};
  const all = {nodes:[entry, bridge, destination], edges:[first, second]};
  await page.route("**/api/graph**", route => route.fulfill({json:{...all, center:null}}));
  await page.route("**/api/notes/entry/neighbors**", route => {
    const params = new URL(route.request().url()).searchParams;
    const data = params.get("type") === "guide" ? {nodes:[entry], edges:[]}
      : params.get("hops") === "2" ? all : {nodes:[entry,bridge], edges:[first]};
    return route.fulfill({json:{...data,center:"entry"}});
  });
  await page.goto("/graph");
  const explore = (title: string) => page.getByRole("button", {name:"Explore " + title, exact:true});
  await explore("Entry").click();
  await page.getByRole("radio",{name:"2 hops",exact:true}).check();
  await expect(explore("Destination")).toBeVisible();
  await expect(page.locator('[data-renderer="fallback"]')).toBeVisible();
  await page.getByText("Connections", {exact:true}).click();
  const connections = page.getByRole("region",{name:"Graph connections"}).getByRole("listitem");
  await expect(connections).toHaveCount(2);
  await expect(connections.filter({hasText:"Entry → Bridge · related"})).toBeVisible();
  await expect(connections.filter({hasText:"Bridge → Destination · wikilink"})).toBeVisible();
  await expect(connections.filter({hasText:"Entry → Destination"})).toHaveCount(0);

  const response = page.waitForResponse(res => new URL(res.url()).pathname === "/api/notes/entry/neighbors");
  await page.getByRole("combobox",{name:"Type",exact:true}).selectOption("guide");
  expect(Object.fromEntries(new URL((await response).url()).searchParams)).toEqual({history:"false",hops:"2",type:"guide"});
  await expect(explore("Entry")).toBeVisible();
  await expect(explore("Bridge")).toHaveCount(0);
  await expect(explore("Destination")).toHaveCount(0);
  await expect(connections).toHaveCount(0);
});

test("connections mount on request and distinguish notes sharing a title", async ({page}) => {
  await page.route("**/api/session", route => route.fulfill({json:{ceiling:"restricted",csrf_token:"fixture",absolute_remaining_seconds:28800}}));
  await page.route("**/api/graph**", route => route.fulfill({json:{
    nodes:[{...guide, title:"Index"}, {...hub, title:"Index"}], edges:[edge], center:null,
  }}));
  await page.goto("/graph");
  const trigger = page.getByText("Connections", {exact:true});
  await expect(trigger).toBeVisible();
  await expect(page.locator('[aria-label="Graph connections"] li')).toHaveCount(0);
  await trigger.focus();
  await trigger.press("Enter");
  const row = page.getByRole("region", {name:"Graph connections"}).getByRole("listitem");
  for (const text of ["guide", "catalog", "related"]) {
    await expect(row.getByText(text, {exact:true})).toHaveAttribute("translate", "no");
  }
  await expect(row).toContainText("Index (guide) → Index (catalog)");
});

test("selecting a note shows its panel with open, edit and neighborhood actions", async ({page}) => {
  await session(page);
  await page.route("**/api/graph**", route => route.fulfill({json:{nodes:[guide, hub], edges:[edge], center:null}}));
  await page.route("**/api/notes/guide**", route => route.fulfill({json:{...guide, updated:"2026-11-20", content_hash:"a".repeat(64)}}));
  await page.route("**/api/notes/guide/neighbors**", route => route.fulfill({json:{nodes:[guide, hub], edges:[edge], center:"guide"}}));
  await page.goto("/graph");
  const panel = page.getByRole("complementary", {name:"Selected note"});
  await expect(panel).toContainText("Select a note");
  const select = page.getByRole("button", {name:"Select Field guide", exact:true});
  await select.click();
  await expect(select).toHaveAttribute("aria-pressed", "true");
  await expect(panel.getByRole("heading", {name:"Field guide", exact:true})).toBeVisible();
  for (const text of ["research · guide", "P1", "internal", "verified"]) await expect(panel.getByText(text, {exact:true})).toBeVisible();
  await expect(panel.locator("time")).toHaveAttribute("datetime", "2026-11-20");
  await expect(panel.getByRole("link", {name:"Open note"})).toHaveAttribute("href", "/notes/guide");
  await expect(panel.getByRole("link", {name:"Edit"})).toHaveAttribute("href", "/editor/guide");
  // A filter that hides the selected note clears the panel.
  await page.getByRole("combobox", {name:"Type", exact:true}).selectOption("hub");
  await expect(panel).toContainText("Select a note");
  await page.getByRole("combobox", {name:"Type", exact:true}).selectOption("");
  await panel.getByRole("button", {name:"Show neighborhood"}).click();
  await expect(page.getByRole("button", {name:"Back to global graph"})).toBeVisible();
});
