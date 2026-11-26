import { expect, test, type Page } from "@playwright/test";

const item = {id:"pr-1", status:"needs_owner", kind:"update", revision:3, target:"sample", source_agent:"fixture", conflict:false};
const valid = {ok:true, violations:[]};
const candidate = {id:"sample",title:"Example note",body:"## Candidate\n\n<script>plain example</script>",lang:"en"};
const detail = {...item, base_label:"base", base:{...candidate,body:"Before"}, candidate,
  stale_base:false, validation:valid, historical_validation:valid,
  decision:{kind:"update", target:"sample",reason:"Owner review."},
  diff:{fields:{summary:{before:"Old summary",after:"New summary"}},body:"--- base\n+++ candidate\n@@ -1 +1 @@\n-Before\n+After", truncated:false}};
async function setup(page:Page) {
  await page.route("**/api/session", r => r.fulfill({json:{ceiling:"restricted",csrf_token:"test",absolute_remaining_seconds:28800}}));
  await page.route("**/api/inbox",r => r.fulfill({json:{items:[item]}}));
  await page.route("**/api/inbox/pr-1",r => r.fulfill({json:detail}));
  await page.goto("/inbox");
  await page.getByRole("button",{name:"Review pr-1"}).click();
}

test("owner inspects the sanitized preview and applies only the displayed revision", async ({page}) => {
  await setup(page);
  await expect(page.getByText("Old summary")).toBeVisible();
  await expect(page.getByText("+After",{exact:true})).toBeVisible();
  await page.getByText("Candidate preview",{exact:true}).click();
  await expect(page.getByRole("heading",{name:"Candidate",exact:true})).toBeVisible();
  await expect(page.locator(".markdown-body script")).toHaveCount(0);
  await page.route("**/api/inbox/pr-1/approve",r => {
    expect(r.request().postDataJSON()).toEqual({revision:3});
    expect(r.request().headers()["x-csrf-token"]).toBe("test");
    return r.fulfill({json:{status:"applied",outcome:"applied",commit:"abc123",warnings:[{code:"previous_version_in_history",message:"Prior version remains in history."}]}});
  });
  await page.getByRole("button",{name:"Approve and apply"}).click();
  await expect(page.getByText("abc123")).toBeVisible();
  await expect(page.getByText("Prior version remains in history.")).toBeVisible();
});

test("a stale inspection requires reload and shows current versus empty base labels", async ({page}) => {
  await setup(page);
  await page.route("**/api/inbox/pr-1/approve",r => r.fulfill({status:409,json:{error:"Conflict."}}));
  await page.getByRole("button",{name:"Approve and apply"}).click();
  await expect(page.getByRole("button",{name:"Approve and apply"})).toBeDisabled();
  await page.route("**/api/inbox/pr-1", r=>r.fulfill({json:{...detail,revision:4,base_label:"current",stale_base:true,validation:null}}));
  await page.getByRole("button",{name:"Reload proposal"}).click();
  await expect(page.getByText("The target changed after submission.")).toBeVisible();
  await expect(page.getByRole("button",{name:"Approve and apply"})).toBeDisabled();
  await page.route("**/api/inbox/pr-1", r=>r.fulfill({json:{...detail,kind:"create",base_label:"empty",base:null,diff:{fields:{summary:{before:null,after:"Initial summary"}},body:"--- empty\n+++ candidate\n@@ -0,0 +1 @@\n+First line",truncated:false}}}));
  await page.reload();
  await page.getByRole("button",{name:"Review pr-1"}).click();
  await expect(page.getByText("New note",{exact:true})).toBeVisible();
  await expect(page.getByRole("columnheader",{name:"Empty base"})).toBeVisible();
  await expect(page.getByRole("cell",{name:"—",exact:true})).toBeVisible();
  await expect(page.getByText("+First line",{exact:true})).toBeVisible();
  await expect(page.locator(".diff-remove").filter({hasNotText:"--- empty"})).toHaveCount(0);
});

test("amendments display field-safe validation and rejection requires a reason", async ({page}) => {
  await setup(page);
  await page.getByRole("button",{name:"Amend Markdown"}).click();
  await page.getByLabel("Replacement Markdown").fill("modified");
  await page.route("**/api/inbox/pr-1/amend", r => {
    expect(r.request().postDataJSON()).toEqual({revision:3,body:"modified"});
    return r.fulfill({status:422,json:{validation:{ok:false,violations:[{code:"secret_detected",field:"body",line:1,severity:"error"}]}}});
  });
  await page.getByRole("button",{name:"Save amendment"}).click();
  await expect(page.getByText("secret_detected",{exact:true})).toBeVisible();
  await expect(page.getByLabel("Replacement Markdown")).toHaveValue("modified");
  await page.getByRole("button",{name:"Cancel amendment"}).click();
  await page.getByRole("button",{name:"Reject proposal"}).click();
  await expect(page.getByRole("button",{name:"Confirm rejection"})).toBeDisabled();
  await page.getByLabel("Reason for rejection").fill("Obsolete request");
  await page.route("**/api/inbox/pr-1/reject", r => {
    expect(r.request().postDataJSON()).toEqual({revision:3, reason:"Obsolete request"});
    return r.fulfill({json:{id:"pr-1",status:"rejected"}});
  });
  await page.getByRole("button",{name:"Confirm rejection"}).click();
  await expect(page.getByText("Proposal rejected.")).toBeVisible();
});

test("approved revisions resume from metadata without retrieving a rejected payload", async ({page}) => {
  await setup(page);
  const statuses = [{...item, status:"approved",revision:4}, {...item,id:"pr-2",status:"rejected"}];
  await page.route("**/api/inbox",r=>r.fulfill({json:{items:statuses}}));
  let reads = 0;
  await page.route("**/api/inbox/pr-?",r => {reads++; return r.fulfill({status:409,json:{}});});
  await page.reload();
  await page.getByRole("button",{name:"Review pr-2"}).click();
  await expect(page.getByText("No reviewable candidate is available.")).toBeVisible();
  await page.getByRole("button",{name:"Review pr-1"}).click();
  await page.route("**/api/inbox/pr-1/resume",r=>{
    expect(r.request().postDataJSON()).toEqual({revision:4});
    return r.fulfill({json:{outcome:"already_applied",status:"applied",commit:"abc123",warnings:[]}});
  });
  await page.getByRole("button",{name:"Resume application"}).click();
  await expect(page.getByText("abc123")).toBeVisible();
  expect(reads).toBe(0);
});

test("an external revision is locked until the owner reloads the inspected proposal",async({page})=>{
  await setup(page);
  await expect(page.getByText("Old summary")).toBeVisible();
  await page.route("**/api/inbox",r=>r.fulfill({json:{items:[{...item,revision:4}]}}));
  await page.route("**/api/inbox/pr-1",r=>r.fulfill({json:{...detail,revision:4,diff:{...detail.diff,fields:{summary:{before:"External change",after:"Revised"}}}}}));
  await page.getByRole("button",{name:"Refresh inbox"}).click();
  await expect(page.getByRole("button",{name:"Approve and apply"})).toBeDisabled();
  await expect(page.getByText("Old summary")).toBeVisible();
  await page.getByRole("button",{name:"Reload proposal"}).click();
  await expect(page.getByText("External change")).toBeVisible();
  await expect(page.getByRole("button",{name:"Approve and apply"})).toBeEnabled();
});

test("truncated diffs expose both full bodies and link only visible notes", async({page})=>{
  await setup(page);
  await page.route("**/api/graph?history=false",r=>r.fulfill({json:{nodes:[{id:"visible"}],edges:[]}}));
  await page.route("**/api/inbox/pr-1",r=>r.fulfill({json:{...detail,base:{body:"Removed after line 512"},candidate:{...candidate,body:"Keep [[visible]] and [[hidden]]."},diff:{fields:{},body:"",truncated:true}}}));
  await page.reload();
  await page.getByRole("button",{name:"Review pr-1"}).click();
  await expect(page.getByText("No Markdown changes.")).toHaveCount(0);
  await page.getByText("Full Markdown comparison").click();
  await expect(page.getByText("Removed after line 512")).toBeVisible();
  await expect(page.locator("pre").filter({hasText:"Keep [[visible]] and [[hidden]]."})).toBeVisible();
  await page.getByText("Candidate preview").click();
  await expect(page.locator('.markdown-body a[href="/notes/visible"]')).toHaveAttribute("href","/notes/visible");
  await expect(page.locator('.markdown-body a[href="/notes/hidden"]')).toHaveCount(0);
});

test("a successful write reconciles the listing after its review panel is closed",async({page})=>{
  await setup(page);
  let applied = false;
  let finish!:()=>void;
  await page.route("**/api/inbox",r=>r.fulfill({json:{items:[{...item,status:applied?"applied":"needs_owner"}, {...item,id:"pr-2",status:"rejected"}]}}));
  await page.reload();
  await page.getByRole("button",{name:"Review pr-1"}).click();
  await page.route("**/api/inbox/pr-1/approve",async r=>{
    await new Promise<void>(resolve=>{finish=resolve;}); applied=true;
    await r.fulfill({json:{outcome:"applied",status:"applied",commit:"abc",warnings:[]}});
  });
  await page.getByRole("button",{name:"Approve and apply"}).click();
  await expect.poll(()=>Boolean(finish)).toBe(true);
  await page.getByRole("button",{name:"Review pr-2"}).click();
  finish();
  await expect(page.getByRole("button",{name:"Review pr-1"})).toContainText("applied");
});

test("an externally approved item resumes its listed revision despite an open amendment",async({page})=>{
  await setup(page);
  await page.getByRole("button",{name:"Amend Markdown"}).click();
  await page.route("**/api/inbox",r=>r.fulfill({json:{items:[{...item,revision:4,status:"approved"}]}}));
  await page.getByRole("button",{name:"Refresh inbox"}).click();
  await page.route("**/api/inbox/pr-1/resume",r=>{
    expect(r.request().postDataJSON()).toEqual({revision:4});
    return r.fulfill({json:{outcome:"applied",commit:"xyz"}});
  });
  await page.getByRole("button",{name:"Resume application"}).click();
  await expect(page.getByText("xyz")).toBeVisible();
});

test("changing the note base locks a previously valid proposal at the same revision",async({page})=>{
  await setup(page);
  await expect(page.getByRole("button",{name:"Approve and apply"})).toBeEnabled();
  await page.route("**/api/inbox/pr-1",r=>r.fulfill({json:{...detail,stale_base:true,base_label:"current",validation:null}}));
  await page.getByRole("button",{name:"Refresh inbox"}).click();
  await expect(page.getByRole("button",{name:"Approve and apply"})).toBeDisabled();
  await expect(page.getByText("Old summary")).toBeVisible();
  await page.getByRole("button",{name:"Reload proposal"}).click();
  await expect(page.getByText("The target changed after submission.")).toBeVisible();
});

test("a late amendment conflict reconciles an externally approved item",async({page})=>{
  await setup(page);
  let approved=false;
  let release!:()=>void;
  await page.route("**/api/inbox",r=>r.fulfill({json:{items:[{...item,status:approved?"approved":"needs_owner",revision:approved?4:3},{...item,id:"pr-2",status:"rejected"}]}}));
  await page.reload();
  await page.getByRole("button",{name:"Review pr-1"}).click();
  await page.getByRole("button",{name:"Amend Markdown"}).click();
  await page.getByLabel("Replacement Markdown").fill("Local fixture");
  await page.route("**/api/inbox/pr-1/amend",async r=>{
    await new Promise<void>(resolve=>{release=resolve;}); approved=true; await r.fulfill({status:409,json:{}});
  });
  await page.getByRole("button",{name:"Save amendment"}).click();
  await expect.poll(()=>Boolean(release)).toBe(true);
  await page.getByRole("button",{name:"Review pr-2"}).click();
  release();
  await expect(page.getByRole("button",{name:"Review pr-1"})).toContainText("approved");
  await page.getByRole("button",{name:"Review pr-1"}).click();
  await expect(page.getByRole("button",{name:"Resume application"})).toBeEnabled();
});
