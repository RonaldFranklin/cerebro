import { expect, test } from "@playwright/test";

test("reader preserves history across sections and masks unavailable notes", async ({ page }) => {
  await page.route("**/api/session", route => route.fulfill({json: {
    ceiling: "internal", csrf_token: "fixture", absolute_remaining_seconds: 28800,
  }}));
  await page.route("**/api/notes/**", route => {
    const url = new URL(route.request().url());
    if (url.pathname !== "/api/notes/fixture-guide") {
      return route.fulfill({status:404, json:{error:"Not found."}});
    }
    const section = url.searchParams.get("section");
    if (url.searchParams.get("history") !== "true" || (section && section !== "Working copy")) {
      return route.fulfill({status:404,json:{error:"Not found."}});
    }
    return route.fulfill({json:{
      id:"fixture-guide", title:"Working notes", type:"guide", area:"research", priority:"P2", status:"archived",
      summary:"Fixture summary", sensitivity:"internal", related:[], hub:"fixture-hub", superseded_by:null,
      lang:"es", created:"2026-01-01", updated:"2026-01-02", review_by:null, members:null, section,
      body: section ? "## Working copy\n\nSelected content." : "## Working copy\n\nFull content.\n\n## Other section\n",
    }});
  });
  await page.goto("/notes/fixture-guide?history=true");
  await expect(page.getByRole("heading", {name:"Working notes"})).toBeVisible();
  await page.getByRole("navigation", {name:"Sections"}).getByRole("link",{name:"Working copy"}).click();
  await expect(page.getByText("Selected content.")).toBeVisible();
  await expect(page.getByRole("link",{name:"Read full note"})).toHaveAttribute("href","/notes/fixture-guide?history=true");
  await page.goBack();
  await expect(page.getByText("Full content.")).toBeVisible();
  await page.getByRole("link",{name:"fixture-hub"}).click();
  await expect(page.getByRole("alert")).toHaveText("This note or section is unavailable.");
  await expect(page.getByText("Full content.")).toHaveCount(0);
});
