import { expect, test, type Page } from "@playwright/test";

async function owner(page: Page, ceiling = "restricted") {
  await page.route("**/api/session", route => route.fulfill({json:{ceiling,csrf_token:"fixture",absolute_remaining_seconds:28800}}));
  await page.route("**/api/graph?history=true", route => route.fulfill({json:{
    nodes:[{id:"guide"},{id:"index"},{id:"core-budget"}], edges:[],center:null,
  }}));
}

const clean = {ok:true,notes_checked:4,search_index_checked:true,issues:[]};
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

test("a lower-ceiling session never requests the health report", async ({page}) => {
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
