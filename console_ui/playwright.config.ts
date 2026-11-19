import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
import { cpSync, mkdirSync } from "node:fs";
const root = fileURLToPath(new URL("..", import.meta.url));
if (!process.env.P31_RUN_DIR)
  process.env.P31_RUN_DIR = resolve(root, ".cache/p31/run-" + Date.now());
mkdirSync(process.env.P31_RUN_DIR, { recursive: true });
const directory = resolve(process.env.P31_RUN_DIR);
cpSync(resolve(root, "console_ui/dist"), resolve(directory, "dist"), { recursive: true });
export default defineConfig({
  testDir: "./acceptance",
  workers: 1,
  retries: 0,
  timeout: 60000,
  expect: { timeout: 5000 },
  reporter: [
    ["list"],
    ["json", { outputFile: resolve(directory, "playwright-result.json") }],
  ],
  outputDir: resolve(directory, "browser-output"),
  use: {
    baseURL: "http://127.0.0.1:18789",
    channel: "msedge",
    headless: true,
    viewport: { width: 1440, height: 1000 },
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
    serviceWorkers: "block",
  },
  webServer: {
    command: `"${process.env.AXI_TEST_PYTHON || resolve(root, ".venv/Scripts/python.exe")}" -B -m tests_p3.p31_server`,
    cwd: root,
    url: "http://127.0.0.1:18789/",
    reuseExistingServer: false,
    timeout: 30000,
  },
});
