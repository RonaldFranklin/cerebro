import { chromium, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { preview } from "vite";

const root = "tests/toolchain";
const shell = readFileSync(join(root, "dist/index.html"), "utf8");
const certDir = mkdtempSync(join(tmpdir(), "cerebro-toolchain-"));
const cert = join(certDir, "cert.pem");
const key = join(certDir, "key.pem");

try {
  execFileSync("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes",
    "-keyout", key, "-out", cert, "-subj", "/CN=localhost", "-days", "1"], { stdio: "ignore" });
  const server = await preview({
    root,
    preview: {
      host: "127.0.0.1", port: 0,
      https: { cert: readFileSync(cert), key: readFileSync(key) },
    },
    plugins: [{
      name: "fixture-csp",
      configurePreviewServer(server) {
        server.middlewares.use((request, response, next) => {
          if (request.url !== "/") return next();
          const nonce = randomBytes(24).toString("base64");
          response.setHeader("Content-Type", "text/html");
          response.setHeader("Cache-Control", "no-store");
          response.setHeader("Content-Security-Policy",
            `default-src 'none'; script-src 'self'; style-src 'self' 'nonce-${nonce}'; ` +
            "style-src-attr 'none'; connect-src 'self'; img-src 'self'; font-src 'self'; " +
            "worker-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'; form-action 'self'");
          response.end(shell.replace("<main", `<main nonce="${nonce}"`));
        });
      },
    }],
  });
  try {
    const address = server.httpServer.address();
    if (!address || typeof address === "string") throw new Error("Missing TCP port");
    const browser = await chromium.launch();
    try {
      const page = await browser.newPage({ ignoreHTTPSErrors: true });
      await page.addInitScript(() => {
        document.addEventListener("securitypolicyviolation", event => {
          document.documentElement.dataset.violation = event.effectiveDirective;
        });
      });
      const response = await page.goto(`https://127.0.0.1:${address.port}/`);
      const policy = await response?.headerValue("Content-Security-Policy");
      expect(policy).toContain("default-src 'none'");
      expect(policy).toMatch(/style-src 'self' 'nonce-[A-Za-z0-9+/=]+'/);
      await expect(page.locator(".cm-content")).toHaveText("Toolchain test");
      await expect(page.locator(".cm-editor")).toHaveCSS("display", "flex");
      const html = page.locator("html");
      expect(await html.getAttribute("data-violation")).toBeNull();
      await page.evaluate(() => {
        const style = document.createElement("style");
        style.textContent = ".cm-editor { display: none !important }";
        document.head.append(style);
      });
      await expect(html).toHaveAttribute("data-violation", "style-src-elem");
      await expect(page.locator(".cm-editor")).toHaveCSS("display", "flex");
    } finally {
      await browser.close();
    }
  } finally {
    await server.close();
  }
} finally {
  rmSync(certDir, { recursive: true, force: true });
}
