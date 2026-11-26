import { expect, test } from "@playwright/test";
import { colorFor, sizeFor } from "../../src/graphAppearance";

test("priority size is strictly descending and a category has a stable color", () => {
  const sizes = ["P0", "P1", "P2", "P3"].map(sizeFor);
  expect(sizes.every((size, i) => size > 0 && (i === 0 || sizes[i - 1] > size))).toBe(true);
  expect(colorFor("research")).toBe(colorFor("research"));
  expect(colorFor("research")).not.toBe(colorFor("infrastructure"));
});

test("the isolated node can be picked on the real Sigma canvas after layout", async ({ page }) => {
  await page.goto("/graph-canvas.html");
  const surface = page.locator('[data-renderer="ready"] .sigma-surface');
  await expect(surface).toBeVisible();
  await page.setViewportSize({width: 860, height: 620});
  await surface.click();
  await expect(page.locator("output")).toHaveText("fixture");
});

test("a click beyond the node radius lands on the background", async ({page}) => {
  await page.goto("/graph-canvas.html");
  const surface = page.locator('[data-renderer="ready"] .sigma-surface');
  await expect(surface).toBeVisible();
  const bounds = await surface.boundingBox();
  if (!bounds) throw new Error("Canvas has no bounds");
  await surface.click({position: {x: bounds.width / 2 + 28, y: bounds.height / 2}});
  await expect(page.locator("output")).toBeEmpty();
});

test("absence of WebGL is reported without breaking the document", async ({page}) => {
  await page.addInitScript(() => {
    const get = HTMLCanvasElement.prototype.getContext;
    Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
      value(this: HTMLCanvasElement, kind: string, ...options: unknown[]) {
        return kind.startsWith("webgl") ? null : Reflect.apply(get, this, [kind, ...options]);
      },
    });
  });
  await page.goto("/graph-canvas.html");
  await expect(page.locator('[data-renderer="fallback"]')).toBeVisible();
  await expect(page.locator('[data-renderer="fallback"]').getByRole("status")).toContainText("node list");
});
