import { expect, test } from "@playwright/test";

const guide = {id:"guide", title:"Field guide", area:"research", type:"guide", priority:"P1", status:"verified", sensitivity:"internal", summary:"Guide."};
const hub = {...guide, id:"catalog", title:"Catalog", type:"hub", priority:"P0"};
const archived = {...guide, id:"archive", title:"Archive", status:"archived", area:"archive", sensitivity:"restricted"};
const edge = {source:"guide", target:"catalog", kind:"related", section:null};

test("global filters, keyboard exploration, hops and return preserve graph context", async ({page}) => {
  await page.route("**/api/session", route => route.fulfill({json:{ceiling:"restricted",csrf_token:"fixture",absolute_remaining_seconds:28800}}));
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
  await local;
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
