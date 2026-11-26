import { defineConfig } from "@playwright/test";
import { tmpdir } from "node:os";
import { join } from "node:path";

export default defineConfig({
  testDir: ".",
  testMatch: "**/*.spec.ts",
  workers: 1,
  timeout: 40_000,
  outputDir: join(tmpdir(), "cerebro-web-e2e", String(process.pid)),
  reporter: "list",
  use: {
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
    screenshot: "off",
    trace: "off",
    video: "off",
  },
});
