import { chromium, expect } from "@playwright/test";
import { preview } from "vite";

const server = await preview({
  root: "tests/toolchain",
  preview: { host: "127.0.0.1", port: 0 },
});
try {
  const address = server.httpServer.address();
  if (!address || typeof address === "string") throw new Error("Missing TCP port");
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    await page.goto(`http://127.0.0.1:${address.port}/`);
    await expect(page.locator(".cm-content")).toHaveText("Toolchain test");
  } finally {
    await browser.close();
  }
} finally {
  await server.close();
}
