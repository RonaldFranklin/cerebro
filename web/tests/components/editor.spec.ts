import { expect, test, type Page } from "@playwright/test";

const oldHash = "a".repeat(64), currentHash = "b".repeat(64), savedHash = "c".repeat(64);
const seed = {
  id:"example",title:"Field guide",summary:"A fixture summary",type:"guide",area:"research",priority:"P2",
  sensitivity:"internal",status:"current",hub:null,related:[],superseded_by:null,lang:"en",review_by:null,
  created:"2026-01-01",updated:"2026-01-01",section:null,members:null,body:"Original body.",content_hash:oldHash,
};

const passing = {validation:{ok:true,violations:[]}, budget:{words:2,soft:1500,hard:3000}, link_lines:[], base_current:true};

async function session(page: Page) {
  await page.route("**/api/session", route => route.fulfill({json:{ceiling:"restricted",csrf_token:"fixture",absolute_remaining_seconds:28800}}));
  await page.route("**/api/graph?*", route => route.fulfill({json:{center:null,edges:[],nodes:[{id:"visible-example",type:"guide",area:"research",priority:"P2",status:"current"}]}}));
  await page.route("**/api/notes/*/validate", route => route.fulfill({json:passing}));
}

test("stale saves keep the draft and require review of the reloaded note", async ({page}) => {
  await session(page);
  let changed = false;
  const writes: Array<{expected_hash:string;note:typeof seed}> = [];
  await page.route("**/api/notes/example*", route => {
    if (route.request().method() === "PUT") {
      writes.push(route.request().postDataJSON());
      if (!changed) { changed = true; return route.fulfill({status:409,json:{error:"Conflict."}}); }
      return route.fulfill({json:{content_hash:savedHash,commit:"deadbeef",validation:{ok:true,violations:[]},
        warnings:[{code:"previous_version_in_history",message:"Earlier versions remain in history."}]}});
    }
    expect(new URL(route.request().url()).searchParams.get("history")).toBe("true");
    return route.fulfill({json:changed ? {...seed,title:"Updated elsewhere",content_hash:currentHash} : seed});
  });
  await page.goto("/editor/example");
  const editor = page.getByRole("textbox",{name:"Markdown body"});
  await editor.fill("Owner draft with [[visible-example]] and [[missing-example]].");
  await page.getByRole("button",{name:"Preview",exact:true}).click();
  await expect(page.getByRole("link",{name:"[[visible-example]]"})).toBeVisible();
  await expect(page.getByRole("link",{name:"[[missing-example]]"})).toHaveCount(0);
  await page.getByRole("button",{name:"Save & commit"}).click();
  await expect(page.getByRole("button",{name:"Save & commit"})).toBeDisabled();
  await page.getByRole("button",{name:"Reload stored note"}).click();
  await expect(page.getByText("Updated elsewhere", {exact:true}).first()).toBeVisible();
  await page.getByRole("button",{name:"Keep my changes"}).click();
  await expect(page.getByLabel("Title",{exact:true})).toHaveValue("Updated elsewhere");
  await page.getByRole("button",{name:"Save & commit"}).click();
  await expect(page.getByText("Changes saved.",{exact:true})).toBeVisible();
  await expect(page.getByText("Earlier versions remain in history.")).toBeVisible();
  expect(writes.map(w => w.expected_hash)).toEqual([oldHash,currentHash]);
  expect(writes[1].note.body).toContain("Owner draft");
  expect(writes[1].note.title).toBe("Updated elsewhere");
  expect(Object.keys(writes[1].note).sort()).toEqual(
    ["area","body","hub","lang","priority","related","review_by","sensitivity","status","summary","superseded_by","title","type"].sort());
});

test("whole-note hash is mandatory and validation preserves the draft", async ({page}) => {
  await session(page);
  let editable = false;
  await page.route("**/api/notes/example*", route => route.fulfill(route.request().method() === "PUT"
    ? {status:422,json:{validation:{ok:false,violations:[{field:"title",severity:"error",code:"title_blank",line:null}]}}}
    : {json:{...seed,content_hash:editable ? oldHash : undefined}}));
  await page.goto("/editor/example");
  await expect(page.getByRole("alert")).toContainText("complete");
  await expect(page.getByRole("textbox",{name:"Markdown body"})).toHaveCount(0);
  editable = true;
  await page.reload();
  await page.getByLabel("Title",{exact:true}).fill("Changed title");
  await page.getByRole("button",{name:"Save & commit"}).click();
  await expect(page.getByText("Title blank", {exact:false})).toBeVisible();
  await expect(page.getByLabel("Title",{exact:true})).toHaveValue("Changed title");
});

test("wikilink completion uses only visible note IDs", async ({page}) => {
  await session(page);
  await page.route("**/api/notes/example*", route => route.fulfill({json:seed}));
  await page.goto("/editor/example");
  const body = page.getByRole("textbox",{name:"Markdown body"});
  await body.fill("[[vis");
  await body.press("End");
  await body.press("Control+Space");
  await expect(page.getByRole("option",{name:"visible-example"})).toBeVisible();
  await page.getByRole("option",{name:"visible-example"}).click();
  await expect(body).toHaveText("[[visible-example]]");
  await body.fill("[[missing");
  await body.press("End");
  await body.press("Control+Space");
  await expect(page.getByRole("listbox")).toHaveCount(0);
});

for (const history of [false, true]) {
  test(`editor completion, preview and reader links honor history=${history}`, async ({page}) => {
    await session(page);
    const graphQueries: string[] = [];
    await page.route("**/api/graph?*", route => {
      const mode = new URL(route.request().url()).searchParams.get("history") ?? "";
      graphQueries.push(mode);
      const ids = mode === "true" ? ["visible-example", "visible-archived"] : ["visible-example"];
      return route.fulfill({json:{center:null,edges:[],nodes:ids.map(id => ({id}))}});
    });
    await page.route("**/api/notes/example*", route => route.fulfill({json:seed}));
    const query = history ? "?history=true" : "";
    await page.goto("/editor/example" + query);
    await expect(page.getByRole("link",{name:"example",exact:true})).toHaveAttribute("href","/notes/example" + query);
    await expect.poll(() => graphQueries).toEqual([String(history)]);
    const body = page.getByRole("textbox",{name:"Markdown body"});
    await body.fill("[[visible-");
    await body.press("End");
    await body.press("Control+Space");
    await expect(page.getByRole("option",{name:"visible-example"})).toBeVisible();
    await expect(page.getByRole("option",{name:"visible-archived"})).toHaveCount(history ? 1 : 0);
    await body.press("Escape");
    await body.fill("[[visible-example]] and [[visible-archived]]");
    await page.getByRole("button",{name:"Preview",exact:true}).click();
    await expect(page.getByRole("link",{name:"[[visible-example]]"})).toHaveAttribute("href","/notes/visible-example" + query);
    const archived = page.getByRole("link",{name:"[[visible-archived]]"});
    if (history) await expect(archived).toHaveAttribute("href","/notes/visible-archived" + query);
    else await expect(archived).toHaveCount(0);
  });
}

test("reopening the editor waits for this visit's read and retry", async ({page}) => {
  await session(page);
  await page.route("**/api/search?*", route => route.fulfill({json:{results:[]}}));
  let reads = 0;
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/notes/example*", async route => {
    reads++;
    if (reads === 1) return route.fulfill({json:seed});
    if (reads === 2) {
      await pending;
      return route.fulfill({status:503,json:{error:"Unavailable."}});
    }
    return route.fulfill({json:{...seed,title:"Updated in another tab",content_hash:currentHash}});
  });
  await page.goto("/editor/example");
  const title = page.getByLabel("Title",{exact:true});
  await expect(title).toHaveValue(seed.title);
  await page.getByRole("link",{name:"Notes",exact:true}).click();
  await expect(page).toHaveURL(/\/search$/);
  await page.goBack();
  await expect.poll(() => reads).toBe(2);
  await expect(title).toHaveCount(0);
  release();
  await expect(page.getByRole("alert")).toContainText("complete");
  await expect(title).toHaveCount(0);
  await page.getByRole("button",{name:"Retry"}).click();
  await expect(title).toHaveValue("Updated in another tab");
  await title.fill("Unsaved local draft");
  const refreshed = page.waitForResponse(response => new URL(response.url()).pathname === "/api/notes/example");
  await page.evaluate(() => {
    window.dispatchEvent(new Event("offline"));
    window.dispatchEvent(new Event("online"));
  });
  await refreshed;
  await expect(title).toHaveValue("Unsaved local draft");
});

const violation = (code: string, severity = "error", field: string | null = "body", line: number | null = null) => ({code, severity, field, line});

test("saving waits for the checks and stays blocked while one fails, broken links included", async ({page}) => {
  await session(page);
  const asked: Array<{expected_hash:string;note:typeof seed}> = [];
  let writes = 0;
  await page.route("**/api/notes/example*", route => {
    if (route.request().method() === "PUT") { writes++; return route.fulfill({json:{content_hash:savedHash,commit:"c",validation:{ok:true,violations:[]},warnings:[]}}); }
    return route.fulfill({json:seed});
  });
  await page.route("**/api/notes/example/validate", route => {
    const body = route.request().postDataJSON();
    expect(route.request().headers()["x-csrf-token"]).toBe("fixture");
    asked.push(body);
    const text: string = body.note.body;
    return route.fulfill({json: text.includes("broken") ? {...passing, link_lines:[1],
      validation:{ok:true,violations:[violation("broken_link","warning")]}}
      : text.includes("long") ? {...passing, budget:{words:1600,soft:1500,hard:3000},
        validation:{ok:true,violations:[violation("body_over_soft_limit","warning")]}} : passing});
  });
  await page.goto("/editor/example");
  const panel = page.getByRole("complementary", {name:"Validation"});
  const save = page.getByRole("button", {name:"Save & commit"});
  await expect(panel.getByRole("status")).toHaveText("All checks pass.");
  await expect(save).toBeDisabled();
  const editor = page.getByRole("textbox",{name:"Markdown body"});
  await editor.fill("A broken link.");
  await expect(panel.getByRole("status")).toHaveText("Saving is blocked while a check fails.");
  await expect(panel.getByRole("listitem").filter({hasText:"Links: Failed"}).first()).toContainText("Line 1 of the body");
  await expect(save).toBeDisabled();
  await page.keyboard.press("Control+s");
  await editor.fill("A long note.");
  await expect(panel.getByRole("listitem").filter({hasText:"Word budget · P2: Warning"}).first()).toContainText("1,600 / 1,500 soft · 3,000 hard");
  await expect(save).toBeEnabled();
  expect(writes).toBe(0);
  expect(asked.at(-1)!.expected_hash).toBe(oldHash);
  expect(asked.at(-1)!.note.body).toBe("A long note.");
  await page.keyboard.press("Control+s");
  await expect(page.getByText("Changes saved.",{exact:true})).toBeVisible();
  expect(writes).toBe(1);
});

test("fast typing asks once and a failed check can be retried", async ({page}) => {
  await session(page);
  let asked = 0;
  await page.route("**/api/notes/example*", route => route.fulfill({json:seed}));
  await page.route("**/api/notes/example/validate", route => ++asked === 2
    ? route.fulfill({status:503,json:{}}) : route.fulfill({json:passing}));
  await page.goto("/editor/example");
  await expect(page.getByText("All checks pass.")).toBeVisible();
  await page.getByLabel("Title",{exact:true}).pressSequentially(" typed quickly");
  await expect(page.getByText("The draft could not be checked. Saving is blocked.")).toBeVisible();
  await expect(page.getByRole("button", {name:"Save & commit"})).toBeDisabled();
  expect(asked).toBe(2);
  await page.getByRole("button", {name:"Retry checks"}).click();
  await expect(page.getByRole("button", {name:"Save & commit"})).toBeEnabled();
});

test("a change made elsewhere opens the comparison before any save", async ({page}) => {
  await session(page);
  await page.route("**/api/notes/example*", route => route.fulfill({json:seed}));
  await page.route("**/api/notes/example/validate", route => route.fulfill({json:{...passing,
    base_current: route.request().postDataJSON().note.title === seed.title}}));
  await page.goto("/editor/example");
  await page.getByLabel("Title",{exact:true}).fill("Mine");
  await expect(page.getByRole("region",{name:"Conflict recovery"})).toBeVisible();
  await expect(page.getByRole("button", {name:"Save & commit"})).toBeDisabled();
});
