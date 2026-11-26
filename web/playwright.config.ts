import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/components",
  use: { baseURL: "http://127.0.0.1:4179", trace: "off", screenshot: "off", video: "off" },
  webServer: {
    command: "pnpm exec vite --config tests/components/vite.config.ts",
    url: "http://127.0.0.1:4179",
    reuseExistingServer: false,
  },
});
