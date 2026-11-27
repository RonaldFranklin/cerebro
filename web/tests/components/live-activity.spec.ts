import { expect, test, type Page } from "@playwright/test";

const guide = {id:"guide", title:"Field guide", area:"research", type:"guide", priority:"P2", status:"current", sensitivity:"internal", summary:"Guide."};
const hub = {...guide, id:"catalog", title:"Catalog", type:"hub", priority:"P1"};
const edge = {source:"guide", target:"catalog", kind:"related", section:null};
const frame = (data: object) => `event: activity\ndata: ${JSON.stringify(data)}\n\n`;

async function graph(page: Page, ceiling = "restricted") {
  await page.route("**/api/session", route => route.fulfill({json:{ceiling,csrf_token:"fixture",absolute_remaining_seconds:28800}}));
  await page.route("**/api/graph**", route => route.fulfill({json:{nodes:[guide, hub], edges:[edge], center:null}}));
}

/** Serves one burst of events, then refuses reconnection so the page settles. */
async function stream(page: Page, events: object[]) {
  const requests: string[] = [];
  await page.route("**/api/activity", route => {
    requests.push(route.request().url());
    return requests.length === 1 ? route.fulfill({status:200, contentType:"text/event-stream",
      body:"retry: 50\n\n" + events.map(frame).join("")}) : route.fulfill({status:403, json:{}});
  });
  return requests;
}

test("agent activity appears in the feed and pulses on its note", async ({page}) => {
  await graph(page);
  const now = new Date().toISOString();
  const read = {note_id:"guide", verb:"read", client:"claude-code", at:now};
  const old = {note_id:"catalog", verb:"write", client:"codex", at:new Date(Date.now() - 5 * 60_000).toISOString()};
  await stream(page, [old, read, read, {note_id:5, verb:"delete"}]);
  await page.goto("/graph");
  const feed = page.getByRole("region", {name:"Live activity"});
  const rows = feed.getByRole("listitem");
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0)).toContainText("read");
  await expect(rows.nth(0)).toContainText("claude-code");
  await expect(rows.nth(0).getByRole("link", {name:"guide"})).toHaveAttribute("href", "/notes/guide?history=true");
  await expect(rows.nth(0)).toContainText("now");
  await expect(rows.nth(1)).toContainText("5 min");
  const pulses = page.locator(".graph-pulses g");
  await expect(pulses).toHaveCount(1);
  await expect(pulses).toHaveAttribute("data-note", "guide");
  await expect(pulses.locator("line.synapse")).toHaveCount(1);
  await expect(pulses.locator("line.synapse")).toHaveCSS("animation-name", "synapse");
  await expect(feed).not.toHaveAttribute("aria-live", /.*/);
  await expect(feed.getByRole("link", {name:"All recent changes"})).toHaveAttribute("href", "/changes");
});

test("reduced motion keeps a static highlight", async ({page}) => {
  await page.emulateMedia({reducedMotion:"reduce"});
  await graph(page);
  await stream(page, [{note_id:"guide", verb:"write", client:"codex", at:new Date().toISOString()}]);
  await page.goto("/graph");
  await expect(page.locator(".graph-pulses .halo")).toHaveCount(1);
  await expect(page.locator(".graph-pulses .synapse")).toHaveCSS("animation-name", "none");
  await expect(page.locator(".graph-pulses .wave")).toBeHidden();
});

test("a refused stream says so and can be retried", async ({page}) => {
  await graph(page);
  let reads = 0;
  await page.route("**/api/activity", route => { reads++; return route.fulfill({status:429, json:{}}); });
  await page.goto("/graph");
  const feed = page.getByRole("region", {name:"Live activity"});
  await expect(feed).toContainText("The activity stream is unavailable.");
  await feed.getByRole("button", {name:"Reconnect"}).click();
  await expect.poll(() => reads).toBe(2);
});

test("a lower-ceiling session never opens the stream", async ({page}) => {
  await graph(page, "internal");
  let reads = 0;
  await page.route("**/api/activity", route => { reads++; return route.fulfill({status:403, json:{}}); });
  await page.goto("/graph");
  await expect(page.getByRole("button", {name:"Explore Field guide"})).toBeVisible();
  await expect(page.getByRole("region", {name:"Live activity"})).toHaveCount(0);
  expect(reads).toBe(0);
});

test("the stream closes while the page is hidden and when the graph is left", async ({page}) => {
  await graph(page);
  await page.addInitScript(() => {
    const Original = window.EventSource;
    const record = {opened: 0, closed: 0};
    Object.assign(window, {streams: record, EventSource: class extends Original {
      constructor(url: string | URL) { super(url); record.opened++; }
      close() { record.closed++; super.close(); }
    }});
  });
  await page.route("**/api/activity", () => new Promise<void>(() => {}));
  await page.route("**/api/overview", route => route.fulfill({json:{core:[], core_words:0, core_truncated:false, areas:[], hubs:[]}}));
  await page.goto("/graph");
  const streams = () => page.evaluate(() => (window as unknown as {streams:{opened:number; closed:number}}).streams);
  await expect.poll(streams).toEqual({opened:1, closed:0});
  const visibility = (value: string) => page.evaluate(state => {
    Object.defineProperty(document, "visibilityState", {configurable:true, get:() => state});
    document.dispatchEvent(new Event("visibilitychange"));
  }, value);
  await visibility("hidden");
  await expect.poll(streams).toEqual({opened:1, closed:1});
  await visibility("visible");
  await expect.poll(streams).toEqual({opened:2, closed:1});
  await page.getByRole("link", {name:"Cérebro home"}).click();
  await expect.poll(streams).toEqual({opened:2, closed:2});
});
