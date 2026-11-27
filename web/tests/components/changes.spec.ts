import { expect, test, type Page } from "@playwright/test";

const row = { revision: "a".repeat(40), note_id: "island-hub", title: "Island hub",
  operation: "update", actor: "fixture-agent", timestamp: "2026-11-26T10:30:00Z" };
const detailPath = "**/api/changes/" + row.revision;

async function owner(page: Page, ceiling = "restricted") {
  await page.route("**/api/session", route => route.fulfill({json:{ceiling, csrf_token:"fixture", absolute_remaining_seconds:28800}}));
}

test("the owner can inspect a diff with the keyboard and open the note", async ({page}) => {
  await owner(page);
  await page.route("**/api/changes", route => route.fulfill({json:{changes:[row]}}));
  let requests = 0;
  await page.route(detailPath, route => {
    requests++;
    return requests === 1 ? route.fulfill({status:404,json:{}}) :
      route.fulfill({json:{...row, diff:"-old\n+<img src=x onerror=alert(1)>\n", truncated:true}});
  });
  await page.goto("/changes");
  const button = page.getByRole("button", {name:/Island hub/});
  await expect(button).toContainText("fixture-agent");
  await expect(button.locator("time")).toHaveAttribute("datetime", row.timestamp);
  expect(requests).toBe(0);
  await button.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("alert")).toContainText("Change details are unavailable.");
  await page.getByRole("button", {name:"Retry"}).click();
  const diff = page.getByRole("region", {name:"Commit diff"});
  await expect(diff).toContainText("+<img src=x onerror=alert(1)>");
  await expect(diff.locator("img")).toHaveCount(0);
  await expect(page.getByText("Only part of this diff is shown.")).toBeVisible();
  await expect(page.getByRole("link", {name:"Open note"})).toHaveAttribute("href","/notes/island-hub?history=true");
  await button.click();
  await expect(diff).toHaveCount(0);
});

test("changes distinguish pending, failed and empty history", async ({page}) => {
  await owner(page);
  let finish!: () => void;
  const release = new Promise<void>(resolve => { finish = resolve; });
  let reads = 0;
  await page.route("**/api/changes", async route => {
    if (++reads === 1) {
      await release;
      return route.fulfill({status:503,json:{}});
    }
    return route.fulfill({json:{changes:[]}});
  });
  await page.goto("/changes");
  await expect(page.getByRole("status")).toHaveText("Loading recent changes…");
  finish();
  await expect(page.getByRole("alert")).toHaveText("Recent changes are unavailable.");
  await page.getByRole("button", {name:"Retry"}).click();
  await expect(page.getByText("No note changes to show.")).toBeVisible();
});

test("lower-ceiling sessions cannot navigate to or request history", async ({page}) => {
  await owner(page, "internal");
  const reads: string[] = [];
  page.on("request", req => { if (new URL(req.url()).pathname.startsWith("/api/changes")) reads.push(req.url()); });
  await page.goto("/changes");
  await expect(page.getByText("This view requires a restricted owner session.")).toBeVisible();
  await expect(page.getByRole("navigation").getByRole("link", {name:"Recent changes"})).toHaveCount(0);
  expect(reads).toEqual([]);
});

test("Load more appends the next page and disappears at the end", async ({page}) => {
  await owner(page);
  await page.route("**/api/changes", route => route.fulfill({json:{changes:[row], next_cursor:row.revision}}));
  await page.route("**/api/changes?cursor=" + row.revision, route => route.fulfill({json:{
    changes:[{...row, revision:"b".repeat(40), title:"Older note"}], next_cursor:null, page_cursor:"older-page",
  }}));
  await page.route("**/api/changes/" + "b".repeat(40) + "?cursor=older-page", route =>
    route.fulfill({json:{...row, diff:"+older", truncated:false}}));
  await page.goto("/changes");
  await page.getByRole("button", {name:"Load more"}).click({timeout:3000});
  await page.getByRole("button", {name:/Older note/}).click();
  await expect(page.getByRole("region", {name:"Commit diff"})).toContainText("+older");
  await expect(page.getByRole("button", {name:/Island hub/})).toBeVisible();
  await expect(page.getByRole("button", {name:"Load more"})).toHaveCount(0);
});

test("retrying an expired continuation reloads the first page", async ({page}) => {
  await owner(page);
  let reads = 0;
  await page.route("**/api/changes", route => route.fulfill({json:{
    changes:++reads === 1 ? [] : [row], next_cursor:reads === 1 ? "expired" : null,
  }}));
  await page.route("**/api/changes?cursor=expired", route => route.fulfill({status:404, json:{}}));
  await page.goto("/changes");
  await expect(page.getByText("No note changes to show.")).toHaveCount(0);
  await page.getByRole("button", {name:"Load more"}).click();
  await page.getByRole("button", {name:"Retry"}).click();
  await expect(page.getByRole("button", {name:/Island hub/})).toBeVisible();
  expect(reads).toBe(2);
});
