import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

export default defineConfig({
  root: fileURLToPath(new URL("./site", import.meta.url)),
  // The layout worker's dependency is invisible to Vite's startup scan. Discovered mid-run, it reloads every open
  // test page, so whichever tests are running then fail at random on a cold cache.
  optimizeDeps: { include: ["graphology-layout-forceatlas2"] },
  server: { host: "127.0.0.1", port: 4179, strictPort: true,
    fs: { allow: [fileURLToPath(new URL("../..", import.meta.url))] } },
});
