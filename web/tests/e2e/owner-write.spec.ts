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

test("inspect an agent’s committed note and explicitly save an owner edit under the production CSP", async ({page, demo}) => {
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
  const prose = page.locator(".markdown-body");
  await expect(prose).toHaveAttribute("lang", "en");
  await expect(prose).toHaveAttribute("translate", "yes");
  const command = prose.locator("pre code");
  await expect(command).toHaveText(/ssh -V/);
  expect(await command.evaluate(node => node.closest('[translate]')?.getAttribute("translate"))).toBe("no");
  await page.getByRole("link",{name:"Graph",exact:true}).click();
  await page.getByRole("button",{name:"Explore SSH access",exact:true}).click();
  await expect(page.getByRole("radio",{name:"1 hop",exact:true})).toBeChecked();
  expect(git("log","-1","--format=%B").split("\n")).toContain("Written-by: fixture-agent");
  await expect(page.getByRole("navigation").getByRole("link",{name:"Inbox",exact:true})).toHaveCount(0);
  await page.goto(demo.private_origin + "/notes/island-hub");
  await expect(page.locator(".markdown-body")).toContainText("Collect the infrastructure handover notes here.");

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
  expect(git("rev-parse","HEAD^")).toBe(before);
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

test("the lower-ceiling listener denies the owner editor and health views", async ({page, demo}) => {
  const privateRequests: string[] = [];
  page.on("request", req => {
    const path = new URL(req.url()).pathname;
    if (path === "/api/health" || path === "/api/notes/server-ssh-access") privateRequests.push(path);
  });
  await page.goto(demo.public_origin + "/editor/server-ssh-access");
  await page.getByLabel("Password",{exact:true}).fill(demo.password);
  await page.getByLabel("Password",{exact:true}).press("Enter");
  await expect(page.getByText("This view requires the private listener.")).toBeVisible();
  await page.goto(demo.public_origin + "/health");
  await expect(page.getByText("This view requires the private listener.")).toBeVisible();
  expect(privateRequests).toEqual([]);
});

test("a suspended editor drops its draft after another tab replaces the session", async ({page, context, demo}) => {
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
  const save = page.getByRole("button",{name:"Save changes",exact:true});
  await expect(save).toBeEnabled();
  await save.focus();
  const bounds = await save.boundingBox();
  if (!bounds) throw new Error("Save control has no geometry");

  const other = await context.newPage();
  await other.goto(demo.private_origin);
  const lifecycle = await context.newCDPSession(page);
  await lifecycle.send("Page.setWebLifecycleState", {state:"frozen"});
  // A same-origin client can replace the cookie without delivering application broadcasts
  // to the frozen editor. Its next focus must revalidate against the new session.
  const statuses = await other.evaluate(async password => {
    const session = await (await fetch("/api/session")).json();
    const logout = await fetch("/api/logout", {method:"POST", headers:{"X-CSRF-Token":session.csrf_token}});
    const login = await fetch("/api/login", {method:"POST", headers:{"Content-Type":"application/json","X-Cerebro-Request":"1"},
      body:JSON.stringify({password})});
    return [logout.status, login.status];
  }, demo.password);
  expect(statuses).toEqual([204,200]);
  let release!: () => void;
  const hold = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/session", async route => {
    await hold;
    await route.continue().catch(() => {});
  });
  await lifecycle.send("Page.setWebLifecycleState", {state:"active"});
  await page.bringToFront();
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  try {
    await expect(page.getByText("Checking session…",{exact:true})).toBeVisible();
    await expect(save).toBeHidden();
    await page.keyboard.press("Enter");
    await page.mouse.click(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
    expect(writes).toEqual([]);
  } finally {
    release();
  }
  await expect(editor).toContainText("Check the client");
  await expect(editor).not.toContainText(draft);
  await expect(page.getByRole("button",{name:"Save changes",exact:true})).toBeDisabled();
  expect(writes).toEqual([]);
  expect(execFileSync("git",["-C",demo.content_repo,"rev-parse","HEAD"],{encoding:"utf8"})).toBe(before);
});
