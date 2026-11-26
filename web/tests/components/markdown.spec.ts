import { expect, test } from "@playwright/test";

test("semantic prose, sections, wikilinks and technical translation boundaries", async ({ page }) => {
  await page.goto("/markdown.html");
  const body = page.locator(".markdown-body");
  await expect(body).toHaveAttribute("lang", "en");
  await expect(body).toHaveAttribute("translate", "yes");
  await expect(body.getByRole("heading", {name: "First section"})).toBeVisible();
  await expect(body.locator("strong")).toHaveText("strong text");
  await expect(body.getByRole("table")).toBeVisible();
  const wiki = body.getByRole("link", { name: "[[valid-note#Setup]]", exact: true });
  await expect(wiki).toHaveAttribute("href", "/notes/valid-note?section=Setup&history=true");
  await expect(wiki).toHaveAttribute("translate", "no");
  for (const token of ["git status", "./notes/demo.md", "uv run pytest", "note-id", "docker compose up -d", "git clean"]) {
    await expect(body.getByText(token, {exact:true})).toHaveAttribute("translate", "no");
  }
  await expect(body.locator('[translate="no"]').filter({hasText: /before continuing|git is|git stores/})).toHaveCount(0);
  await expect(body.getByRole("link", {name: "[[missing-note]]"})).toHaveCount(0);
  await expect(body.locator('[translate="no"]').filter({hasText: "step-by-step"})).toHaveCount(0);
  const title = page.locator(".plain-fixture");
  await expect(title).toHaveText("Use note-id and docker compose up -d step-by-step; *literal*.");
  await expect(title.locator('[translate="no"]')).toHaveText(["note-id", "docker compose up -d"]);
  await expect(body.locator("pre")).toHaveAttribute("translate", "no");
  await expect(body.getByRole("link", {name:"[[example-code]]"})).toHaveCount(0);
  await expect(page.locator("output")).toHaveText(JSON.stringify([{title:"First section",level:1},{title:"Second section",level:2}]));
});

test("HTML stays text and embedded resources never load", async ({ page }) => {
  const external: string[] = [];
  await page.route("https://example.invalid/**", route => {
    external.push(route.request().url());
    return route.abort();
  });
  await page.goto("/markdown.html");
  const body = page.locator(".markdown-body");
  await expect(body).toContainText('<em title="fixture">markup</em>');
  await expect(body.locator("em, img, video, iframe, script, svg, form, style, [style]")).toHaveCount(0);
  await expect(body.getByRole("link", {name:"Website"})).toHaveAttribute("href", "https://example.invalid/guide");
  for (const label of ["invalid URL","network path","internal redirect"]) {
    await expect(body.getByRole("link", {name:label})).toHaveCount(0);
  }
  expect(external).toEqual([]);
});


test("links to other listeners are inert and an unspecified visible set authorizes no wikilinks", async ({ page }) => {
  await page.goto("http://localhost:4179/markdown.html?no-visible-set");
  const body = page.locator(".markdown-body");
  await expect(body).toContainText("Other port");
  for (const label of ["Other port", "dotted host", "[[valid-note#Setup]]"]) {
    await expect(body.getByRole("link", {name:label})).toHaveCount(0);
  }
});
