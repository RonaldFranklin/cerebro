import { expect, test, type Page } from "@playwright/test";

async function owner(page: Page, ceiling = "restricted") {
  await page.route("**/api/session", route => route.fulfill({json:{ceiling,csrf_token:"fixture",absolute_remaining_seconds:28800}}));
  await page.route("**/api/graph?history=true", route => route.fulfill({json:{
    nodes:[{id:"guide"},{id:"index"},{id:"core-budget"}], edges:[],center:null,
  }}));
}

const entrances = [
  {name:"private",mcp_ceiling:"restricted",browser_ceiling:"restricted"},
  {name:"public",mcp_ceiling:"internal",browser_ceiling:"internal"},
];
const summary = {checked_at:"2026-11-27T08:31:00+00:00",content:{revision:"0123456789abcdef".repeat(2) + "01234567",state:"clean"},listeners:entrances,
  last_backup:{state:"recorded",completed_at:"2026-11-27T03:00:00+00:00",content_revision:"f".repeat(40)}};
const clean = {...summary,ok:true,notes_checked:4,search_index_checked:true,issues:[]};
function issue(code: string, subject: string, severity: string, message = `${code} fixture message`) {
  return {code,subject,severity,field:null,message};
}

test("health separates revision and index coverage and links only visible note subjects", async ({page}) => {
  await owner(page);
  await page.route("**/api/health", route => route.fulfill({json:{...clean,ok:false,search_index_checked:false,issues:[
    issue("secret_detected","guide","error"),issue("index_stale","index","error"),issue("orphan_note","missing","warning"),
    issue("core_over_budget","core-budget","warning"),
  ]}}));
  await page.goto("/health");
  await expect(page.getByRole("heading",{name:"Health",exact:true})).toBeVisible();
  await expect(page.getByText("Out of date",{exact:true})).toBeVisible();
  await expect(page.getByText("Not checked",{exact:true})).toBeVisible();
  await expect(page.getByRole("link",{name:"guide",exact:true})).toHaveAttribute("href","/notes/guide?history=true");
  for (const name of ["missing","index","core-budget"]) await expect(page.getByRole("link",{name,exact:true})).toHaveCount(0);
  await expect(page.getByText("orphan_note fixture message",{exact:true})).toBeVisible();
  await expect(page.getByText("secret_detected",{exact:true})).toBeVisible();
  await page.getByLabel("Severity").selectOption("warning");
  await expect(page.getByText("secret_detected",{exact:true})).toHaveCount(0);
  await expect(page.getByText("orphan_note",{exact:true})).toBeVisible();
});

test("health distinguishes errors, empty reports and partially checked reports on refresh", async ({page}) => {
  await owner(page);
  let reads = 0;
  await page.route("**/api/health", route => {
    expect(route.request().method()).toBe("GET");
    reads++;
    return reads === 1 ? route.fulfill({status:503,json:{error:"Unavailable."}})
      : route.fulfill({json:{...clean,search_index_checked:reads > 2}});
  });
  await page.goto("/health");
  await expect(page.getByRole("alert")).toContainText("unavailable");
  await page.getByRole("button",{name:"Retry"}).click();
  await expect(page.getByText("Not checked",{exact:true})).toBeVisible();
  await expect(page.getByText("No findings in the completed checks.")).toBeVisible();
  await page.getByRole("button",{name:"Refresh report"}).click();
  await expect(page.getByText("Not checked",{exact:true})).toHaveCount(0);
  await expect(page.getByText("No findings in the completed checks.")).toBeVisible();
});

test("the public listener never requests the health report", async ({page}) => {
  await owner(page,"internal");
  let reads = 0;
  await page.route("**/api/health", route => { reads++; return route.fulfill({json:clean}); });
  await page.goto("/health");
  await expect(page.getByRole("link",{name:"Back to overview"})).toBeVisible();
  expect(reads).toBe(0);
});

test("health shows entry drift even when revisions match and omits links if graph reads fail", async ({page}) => {
  await owner(page);
  await page.route("**/api/graph?history=true", route => route.fulfill({status:503,json:{error:"Unavailable."}}));
  await page.route("**/api/health", route => route.fulfill({json:{...clean,ok:false,issues:[
    issue("graph_index_stale","guide","error"), issue("search_index_stale","guide","error"),
  ]}}));
  await page.goto("/health");
  await expect(page.getByText("Matches indexes",{exact:true})).toBeVisible();
  await expect(page.getByText("Entries differ",{exact:true})).toHaveCount(2);
  await expect(page.getByText("guide",{exact:true})).toHaveCount(2);
  await expect(page.getByRole("link",{name:"guide",exact:true})).toHaveCount(0);
});

test("health names problem note files with their reason and never links them", async ({page}) => {
  await owner(page);
  await page.route("**/api/health", route => route.fulfill({json:{...clean,ok:false,issues:[
    issue("note_file_excluded","notes/guide.md","error","invalid frontmatter (left out of reads, search and the graph until fixed)"),
    issue("note_file_blocking","notes/draft.md","error","differs from the last commit (index rebuilds and writes fail until it is fixed)"),
  ]}}));
  await page.goto("/health");
  await expect(page.getByText("Committed file is not a valid note",{exact:true})).toBeVisible();
  await expect(page.getByText("notes/guide.md",{exact:true})).toBeVisible();
  await expect(page.getByText(/^invalid frontmatter \(left out/)).toBeVisible();
  await expect(page.getByText(/^differs from the last commit/)).toBeVisible();
  await expect(page.getByRole("link",{name:"notes/guide.md"})).toHaveCount(0);
});

test("health summarizes the repository and warns about quarantined plaintext", async ({page}) => {
  await owner(page);
  await page.route("**/api/health", route => route.fulfill({json:{...clean,issues:[
    issue("changes_quarantined","quarantine-one","warning"), issue("changes_quarantined","quarantine-two","warning"),
  ]}}));
  await page.goto("/health");
  const summary = page.getByRole("region", {name:"Report summary"});
  const quarantine = summary.locator("div[data-card=quarantine]");
  await expect(quarantine).toContainText("2 folders");
  await expect(quarantine).toContainText("Plaintext — review and remove by hand");
  await expect(quarantine).toHaveAttribute("data-tone", "warning");
  await expect(summary.locator("div[data-card=content]")).toContainText("Clean");
  await expect(summary.locator("div[data-card=content]")).toContainText("4 notes checked");
  await expect(page.getByText("Warnings found", {exact:true})).toBeVisible();
  await expect(page.getByText(/^Checked /)).toBeVisible();
});

test("health does not claim index results when blocked files stopped the checks", async ({page}) => {
  await owner(page);
  await page.route("**/api/health", route => route.fulfill({json:{...clean,ok:false,notes_checked:0,search_index_checked:false,
    content:{revision:null,state:"attention"},issues:[issue("note_file_blocking","notes/draft.md","error")]}}));
  await page.goto("/health");
  await expect(page.getByText("Not checked", {exact:true})).toHaveCount(3);
  await expect(page.locator("div[data-card=content]")).toContainText("Needs attention");
  await expect(page.locator("div[data-card=quarantine]")).toContainText("None");
  await expect(page.getByText("All systems normal")).toHaveCount(0);
});

test("an unstable scan keeps the computed revision result and says nothing about the search limit", async ({page}) => {
  await owner(page);
  let stale = true;
  await page.route("**/api/health", route => route.fulfill({json:{...clean,ok:false,notes_checked:4,search_index_checked:false,issues:[
    issue("note_file_blocking","notes/draft.md","error"), ...stale ? [issue("index_stale","index","error")] : [],
  ]}}));
  await page.goto("/health");
  const revision = page.locator("div[data-card=revision]");
  await expect(revision).toContainText("Out of date");
  await expect(page.getByText("Not checked", {exact:true})).toHaveCount(2);
  await expect(page.getByText("search comparison limit")).toHaveCount(0);
  stale = false;
  await page.getByRole("button",{name:"Refresh report"}).click();
  await expect(revision).toContainText("Matches indexes");
});

test.describe("server facts", () => {
  test.use({timezoneId: "UTC"});

  test("health shows the server's check time, the content revision and each entrance", async ({page}) => {
    await owner(page);
    await page.route("**/api/health", route => route.fulfill({json:clean}));
    await page.goto("/health");
    await expect(page.locator("header time")).toHaveAttribute("datetime", clean.checked_at);
    await expect(page.locator("header time")).toContainText("08:31");
    await expect(page.locator("div[data-card=content]")).toContainText("HEAD 0123456");
    const section = page.getByRole("region", {name:"Entrances"});
    await expect(section.getByRole("heading", {level:3})).toHaveText(["Private", "Public"]);
    const open = section.locator("li").nth(1);
    await expect(open).toContainText("MCP ceiling internal");
    await expect(open).toContainText("Owner browser internal");
    await expect(section.locator("li").first().locator("[data-tone=warning]")).toHaveCount(2);
    await expect(open.locator("[data-tone=warning]")).toHaveCount(0);
  });
});

test.describe("backup record", () => {
  test.use({timezoneId: "UTC"});

  for (const [name, record, value, detail, warns] of [
    ["recent", clean.last_backup, "2026-11-27", "Content fffffff + state", false],
    ["old", {...clean.last_backup, completed_at:"2026-11-25T08:30:00+00:00"}, "2026-11-25", "Older than 48 hours", true],
    ["missing", {state:"never",completed_at:null,content_revision:null}, "Never recorded", "left no record", true],
    ["damaged", {state:"unreadable",completed_at:null,content_revision:null}, "Unreadable", "damaged", true],
  ] as const) {
    test(`a ${name} backup is shown with the right warning`, async ({page}) => {
      await owner(page);
      await page.route("**/api/health", route => route.fulfill({json:{...clean,last_backup:record}}));
      await page.goto("/health");
      const card = page.locator("div[data-card=backup]");
      await expect(card).toContainText(value);
      await expect(card).toContainText(detail);
      await expect(card).toHaveAttribute("data-tone", warns ? "warning" : "ok");
      // The record never changes the findings' overall status.
      await expect(page.getByText("All systems normal")).toBeVisible();
    });
  }
});
