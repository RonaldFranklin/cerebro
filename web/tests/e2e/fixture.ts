import { test as base } from "@playwright/test";
import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { once } from "node:events";
import { join } from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";

interface Demo {
  private_origin: string;
  public_origin: string;
  certificate_public_key_pin: string;
  password: string;
  content_repo: string;
}

export const test = base.extend<{ demo: Demo }>({
  demo: async ({}, use) => {
    const backend = fileURLToPath(new URL("../../../backend/", import.meta.url));
    const password = randomBytes(32).toString("base64url");
    const child = spawn(join(backend, ".venv/bin/python"), [
      "-m", "devtools.web_demo", "--dist", "../web/dist", "--automation",
    ], { cwd: backend, stdio: ["pipe", "pipe", "inherit"] });
    const lines = createInterface({ input: child.stdout });
    const exited = once(child, "close");
    const firstLine = once(lines, "line");
    const timer = setTimeout(() => child.kill("SIGTERM"), 25_000);
    try {
      child.stdin.write(password + "\n");
      const [value] = await Promise.race([firstLine, exited]);
      clearTimeout(timer);
      if (typeof value !== "string") throw new Error("The synthetic service did not start");
      const info = JSON.parse(value) as Omit<Demo, "password">;
      await use({ ...info, password });
    } finally {
      clearTimeout(timer);
      lines.close();
      child.stdin.end();
      const terminate = setTimeout(() => child.kill("SIGTERM"), 3_000);
      try {
        await exited;
      } finally {
        clearTimeout(terminate);
      }
    }
  },
  context: async ({ playwright, demo, viewport, deviceScaleFactor }, use) => {
    const browser = await playwright.chromium.launch({
      args: [`--ignore-certificate-errors-spki-list=${demo.certificate_public_key_pin}`],
    });
    try {
      const context = await browser.newContext({viewport, deviceScaleFactor});
      await use(context);
    } finally {
      await browser.close();
    }
  },
});
