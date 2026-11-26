import { expect, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { test } from "./fixture";

async function capture(page: Page, filename: string) {
  if (process.env.CAPTURE_SCREENSHOTS !== "1") return;
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({
    path:fileURLToPath(new URL("../../../docs/screenshots/phase-5/" + filename, import.meta.url)),
    fullPage:true, animations:"disabled",
  });
}

test("approve the inspected proposal and explicitly save a note under the production CSP", async ({page, demo}) => {
  const violations: string[] = [];
  await page.exposeFunction("reportViolation", (directive: string) => violations.push(directive));
  await page.addInitScript(() => document.addEventListener("securitypolicyviolation", event =>
    (window as unknown as {reportViolation(directive:string):void}).reportViolation(event.effectiveDirective)));
  const git = (...args:string[]) => execFileSync("git", ["-C", demo.content_repo, ...args], {encoding:"utf8"}).trim();
  const before = git("rev-parse", "HEAD");
  await page.goto(demo.private_origin + "/search");
  const password = page.getByLabel("Password",{exact:true});
  await password.fill(demo.password);
  await password.press("Enter");
  const query = page.getByRole("searchbox");
  await query.fill("SSH");
  await query.press("Enter");
  await page.getByRole("link",{name:"SSH access",exact:true}).click();
  await expect(page.getByRole("heading",{name:"SSH access",exact:true})).toBeVisible();
  await page.getByRole("link",{name:"Graph",exact:true}).click();
  await page.getByRole("button",{name:"Explore SSH access",exact:true}).click();
  await expect(page.getByRole("radio",{name:"1 hop",exact:true})).toBeChecked();
  await page.getByRole("link",{name:"Inbox",exact:true}).click();
  const review = page.getByRole("button",{name:/^Review /}).first();
  await expect(review).toBeVisible();
  await capture(page, "10-inbox.png");
  await review.click();
  await expect(page.getByText("Markdown changes",{exact:true})).toBeVisible();
  await capture(page, "10-review.png");
  const apply = page.waitForResponse(res => res.url().endsWith("/approve") && res.request().method() === "POST");
  await page.getByRole("button",{name:"Approve and apply",exact:true}).click();
  const approval = await apply;
  expect(approval.status()).toBe(200);
  const applied = await approval.json();
  expect(applied.commit).toBe(git("rev-parse","HEAD"));
  expect(git("rev-parse","HEAD^")).toBe(before);
  for (const line of ["Proposed-by: fixture-agent", "Curated-by: curator/null", "Approved-by: owner"])
    expect(git("log","-1","--format=%B").split("\n")).toContain(line);
  await expect(page.getByText(applied.commit,{exact:false}).first()).toBeVisible();

  await page.goto(demo.private_origin + "/notes/server-ssh-access");
  await page.getByRole("link",{name:"Edit note",exact:true}).click();
  const body = page.getByRole("textbox",{name:"Markdown body"});
  await expect(body).toBeVisible();
  await body.focus();
  await page.keyboard.press("Control+End");
  await page.keyboard.press("Enter");
  await page.keyboard.type("Verified in the disposable browser fixture.");
  await page.keyboard.press("Enter");
  await page.keyboard.type("[[docker-");
  await page.keyboard.press("Control+Space");
  const suggestion = page.getByRole("option", {name:"docker-conventions", exact:true});
  await expect(suggestion).toBeVisible();
  await suggestion.click();
  const geometry = await page.locator(".cm-editor").evaluate(node => ({
    width:node.clientWidth, height:node.clientHeight,
    content:getComputedStyle(node.querySelector(".cm-content")!).whiteSpace,
    nonce:[...document.querySelectorAll("style")].some(tag => tag.nonce &&
      tag.nonce === document.querySelector<HTMLMetaElement>('meta[name="cerebro-style-nonce"]')?.content),
  }));
  expect(geometry.width).toBeGreaterThan(250);
  expect(geometry.height).toBeGreaterThan(90);
  expect(["pre", "pre-wrap", "break-spaces"]).toContain(geometry.content);
  expect(geometry.nonce).toBe(true);
  await capture(page, "10-editor.png");
  await page.getByRole("button",{name:"Preview",exact:true}).click();
  await expect(page.locator(".editor-preview")).toContainText("Verified in the disposable browser fixture.");
  await capture(page, "10-preview.png");
  const save = page.waitForResponse(res => res.request().method() === "PUT" && res.url().endsWith("/api/notes/server-ssh-access"));
  await page.getByRole("button",{name:"Save changes",exact:true}).click();
  const saved = await save;
  expect(saved.status()).toBe(200);
  expect((await saved.json()).commit).toBe(git("rev-parse","HEAD"));
  expect(git("rev-parse","HEAD^")).toBe(applied.commit);
  for (const trailer of ["Proposed-by","Curated-by","Approved-by"])
    expect(git("log","-1","--format=%B").split("\n")).toContain(trailer + ": owner");
  await page.getByRole("button",{name:"Sign out",exact:true}).click();
  await expect(password).toBeVisible();
  await page.reload();
  await expect(password).toBeVisible();
  expect(violations).toEqual([]);
});

test("a concurrent owner edit survives inspection of a conflicted draft", async ({page, context, demo}) => {
  await page.goto(demo.private_origin + "/editor/server-ssh-access");
  await page.getByLabel("Password",{exact:true}).fill(demo.password);
  await page.getByLabel("Password",{exact:true}).press("Enter");
  const body = page.getByRole("textbox",{name:"Markdown body"});
  await body.focus();
  await page.keyboard.press("Control+End");
  await page.keyboard.press("Enter");
  await page.keyboard.type("Pending owner amendment.");
  const other = await context.newPage();
  await other.goto(demo.private_origin + "/editor/server-ssh-access");
  const summary = "Another owner tab updated the runbook summary in this disposable fixture.";
  await other.getByLabel("Summary",{exact:true}).fill(summary);
  await other.getByRole("button",{name:"Save changes",exact:true}).click();
  await expect(other.getByText("Changes saved.",{exact:true})).toBeVisible();
  await other.close();
  const before = execFileSync("git",["-C",demo.content_repo,"rev-parse","HEAD"],{encoding:"utf8"}).trim();
  const conflict = page.waitForResponse(res => res.request().method() === "PUT");
  await page.getByRole("button",{name:"Save changes",exact:true}).click();
  expect((await conflict).status()).toBe(409);
  await expect(body).toContainText("Pending owner amendment.");
  await page.getByRole("button",{name:"Reload stored note",exact:true}).click();
  await expect(page.getByRole("button",{name:"Keep my changes",exact:true})).toBeVisible();
  await capture(page, "10-conflict.png");
  expect(execFileSync("git",["-C",demo.content_repo,"rev-parse","HEAD"],{encoding:"utf8"}).trim()).toBe(before);
  await page.getByRole("button",{name:"Keep my changes",exact:true}).click();
  await expect(page.getByLabel("Summary",{exact:true})).toHaveValue(summary);
  await expect(body).toContainText("Pending owner amendment.");
  await page.getByRole("button",{name:"Save changes",exact:true}).click();
  await expect(page.getByText("Changes saved.",{exact:true})).toBeVisible();
});

test("the lower-ceiling listener denies both owner workspaces", async ({page, demo}) => {
  const privateRequests: string[] = [];
  page.on("request", req => {
    const path = new URL(req.url()).pathname;
    if (path === "/api/inbox" || path === "/api/notes/server-ssh-access") privateRequests.push(path);
  });
  await page.goto(demo.public_origin + "/editor/server-ssh-access");
  await page.getByLabel("Password",{exact:true}).fill(demo.password);
  await page.getByLabel("Password",{exact:true}).press("Enter");
  await expect(page.getByText("This view requires the private listener.")).toBeVisible();
  await page.goto(demo.public_origin + "/inbox");
  await expect(page.getByText("This view requires the private listener.")).toBeVisible();
  expect(privateRequests).toEqual([]);
});

test("signing out and back in from another tab discards the private editor draft", async ({page, context, demo}) => {
  const writes: string[] = [];
  context.on("request", request => {
    if (request.method() === "PUT") writes.push(new URL(request.url()).pathname);
  });
  const before = execFileSync("git",["-C",demo.content_repo,"rev-parse","HEAD"],{encoding:"utf8"});
  await page.goto(demo.private_origin + "/editor/server-ssh-access");
  const password = page.getByLabel("Password",{exact:true});
  await password.fill(demo.password);
  await password.press("Enter");
  const editor = page.getByRole("textbox",{name:"Markdown body"});
  const draft = "Unsaved text from the former session.";
  await editor.fill(draft);
  await expect(page.getByRole("button",{name:"Save changes",exact:true})).toBeEnabled();

  const other = await context.newPage();
  await other.goto(demo.private_origin);
  await other.getByRole("button",{name:"Sign out",exact:true}).click();
  await expect(password).toBeVisible();
  await expect(editor).toHaveCount(0);
  const newLogin = other.getByLabel("Password",{exact:true});
  await newLogin.fill(demo.password);
  await newLogin.press("Enter");
  await expect(other.getByRole("heading",{name:"Overview",exact:true})).toBeVisible();
  await expect(editor).toContainText("Check the client");
  await expect(editor).not.toContainText(draft);
  await expect(page.getByRole("button",{name:"Save changes",exact:true})).toBeDisabled();
  expect(writes).toEqual([]);
  expect(execFileSync("git",["-C",demo.content_repo,"rev-parse","HEAD"],{encoding:"utf8"})).toBe(before);
});
