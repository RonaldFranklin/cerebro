import { expect, test, type Page } from "@playwright/test";

const oldHash = "a".repeat(64), currentHash = "b".repeat(64), savedHash = "c".repeat(64);
const seed = {
  id:"example",title:"Field guide",summary:"A fixture summary",type:"guide",area:"research",priority:"P2",
  sensitivity:"internal",status:"current",hub:null,related:[],superseded_by:null,lang:"en",review_by:null,
  created:"2026-01-01",updated:"2026-01-01",section:null,members:null,body:"Original body.",content_hash:oldHash,
};

async function session(page: Page) {
  await page.route("**/api/session", route => route.fulfill({json:{ceiling:"restricted",csrf_token:"fixture",absolute_remaining_seconds:28800}}));
  await page.route("**/api/graph?*", route => route.fulfill({json:{center:null,edges:[],nodes:[{id:"visible-example"}]}}));
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
  await page.getByRole("button",{name:"Save changes"}).click();
  await expect(page.getByRole("button",{name:"Save changes"})).toBeDisabled();
  await page.getByRole("button",{name:"Reload stored note"}).click();
  await expect(page.getByText("Updated elsewhere", {exact:true}).first()).toBeVisible();
  await page.getByRole("button",{name:"Keep my changes"}).click();
  await expect(page.getByLabel("Title",{exact:true})).toHaveValue("Updated elsewhere");
  await page.getByRole("button",{name:"Save changes"}).click();
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
  await page.getByRole("button",{name:"Save changes"}).click();
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
